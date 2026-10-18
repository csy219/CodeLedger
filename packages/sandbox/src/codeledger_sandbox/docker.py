# 这是 `codeledger_sandbox` 包的核心实现文件，完整封装了基于 Docker 的安全沙箱执行能力。它将底层 Docker 命令、容器安全配置、资源限制、超时回收、结果标准化全部封装在内部，对外仅提供极简的 `run()` 接口，上层业务无需了解任何 Docker 细节，即可获得隔离、可控、可审计的命令执行环境。

# 整个文件自底向上分为四层：

# 1. **内部工具函数层**：提供 Docker 可执行文件自动发现、类型兼容转换两个基础能力
# 2. **数据契约层**：定义配置输入、执行输出两套标准冻结数据结构
# 3. **核心业务类**：`DockerSandbox` 对外统一提供沙箱执行能力
# 4. **接口导出层**：由包入口 `__init__.py` 统一筛选后对外暴露


# - `from __future__ import annotations`：延迟类型注解求值，支持类内前向引用、字符串类型注解，是复杂数据类文件的标准前置声明。
# - `os`：读取系统环境变量，用于 Docker 路径的环境变量配置、Windows 系统路径探测。
# - `shutil`：提供跨平台的 `which()` 方法，用于在系统 PATH 中搜索可执行文件。
# - `subprocess`：调用宿主机 Docker 命令的核心底层库，负责进程启动、输出捕获、超时控制。
# - `uuid`：生成全局唯一的容器名称，避免容器名冲突。
# - `dataclass`：轻量级数据类语法糖，用于快速定义纯数据载体类，无需手写构造函数。
# - `Path`：面向对象的路径处理，比字符串路径更安全、跨平台兼容性更强，用于工作目录校验、路径拼接。
from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path


# **核心作用**：自动查找宿主机上的 `docker` 可执行文件路径，解决 Docker 未加入系统 PATH 时无法直接调用的问题，实现开箱即用。

# 采用**三级降级查找策略**，优先级从高到低：

# 1. **环境变量强制指定**：优先读取 `DOCKER_BIN` 环境变量。用户手动指定的路径优先级最高，用于自定义安装路径、便携版 Docker、多版本共存等特殊场景。
# 2. **系统 PATH 标准查找**：通过 `shutil.which()` 在系统全局 PATH 中搜索，这是 Linux、macOS 以及手动配置过 PATH 的 Windows 环境的标准路径。
# 3. **Windows 默认路径兜底**：针对 Windows 版 Docker Desktop 的默认安装位置做专项探测，路径为 `%LOCALAPPDATA%\Programs\DockerDesktop\resources\bin\docker.exe`。只要是默认安装的 Docker Desktop，即使没配 PATH 也能自动找到。
# 4. **最终兜底**：以上全部失败时返回字符串 `"docker"`，退化为系统默认查找行为，错误在执行阶段明确抛出。

# 设计上兼顾了跨平台兼容性：Linux/macOS 没有 `LOCALAPPDATA` 环境变量，第三级会自动跳过，不会产生副作用。
def _resolve_docker_bin() -> str:
    env_bin = os.environ.get("DOCKER_BIN")
    if env_bin:
        return env_bin
    found = shutil.which("docker")
    if found:
        return found
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidate = Path(local_app_data) / "Programs" / "DockerDesktop" / "resources" / "bin" / "docker.exe"
        if candidate.is_file():
            return str(candidate)
    return "docker"


# 2. `_as_text()` — 输出类型标准化
# **核心作用**：统一将不同类型的输出内容转换为标准字符串，保证 `SandboxResult` 中 `stdout`、`stderr` 字段类型严格为 `str`，消除类型不匹配隐患。

# 处理三种输入情况：

# - 空值 `None`：返回空字符串，避免空值传播导致上层报错
# - 字节类型 `bytes`：用 UTF-8 解码，遇到非法字符用替换符替代，保证不会因为编码问题崩溃
# - 已经是字符串：直接原样返回

# 这个函数同时解决了 `subprocess.TimeoutExpired` 异常对象的 `stdout/stderr` 类型不固定（可能是 bytes 或 None）的问题，从根源上避免了 Pylance 类型报错。
def _as_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value



# 两个使用 `@dataclass(frozen=True)` 装饰的冻结数据类，构成了模块的输入输出标准契约，所有调用方都遵循这两套结构交互。
# 1. `SandboxConfig` — 沙箱配置
# 字段	默认值	作用
# image	codeledger-sandbox:0.1	沙箱使用的 Docker 镜像名与版本
# cpus	1.0	容器 CPU 核心数硬限制
# memory	2g	容器内存硬限制
# pids_limit	256	容器内最大进程数，防止 fork 炸弹
# timeout_seconds	120	单条命令最长执行时间，超时强制终止
# network	none	网络模式，默认完全禁用网络
@dataclass(frozen=True)
class SandboxConfig:
    image: str = "codeledger-sandbox:0.1"
    cpus: str = "1.0"
    memory: str = "2g"
    pids_limit: int = 256
    timeout_seconds: int = 120
    network: str = "none"


# 2. `SandboxResult` — 执行结果
# **定位**：沙箱执行结果的标准输出结构，是全系统通用的数据契约，后续的链路追踪、UI 展示、评测打分、日志审计都基于这个结构。
# 字段覆盖了上层所有需要的信息：

# - 执行的命令、退出码
# - 标准输出、错误输出
# - 是否触发超时
# - 对应容器名称（用于排查问题）
@dataclass(frozen=True)
class SandboxResult:
    command: str
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False
    container_name: str | None = None


# 四、核心业务类：DockerSandbox
# 这是模块对外的核心能力类，封装了沙箱的完整生命周期管理。
class DockerSandbox:
    def __init__(
        self,
        config: SandboxConfig | None = None,
        docker_bin: str | None = None,
    ) -> None:
        self.config = config or SandboxConfig()
        self.docker_bin = docker_bin or _resolve_docker_bin()


    ### 4.2 核心方法：`run()`

    # 这是整个模块唯一对外暴露的业务方法，接收命令、工作目录、可选环境变量，返回标准化的执行结果。内部完整覆盖参数校验、容器创建、安全配置、执行、回收全流程。
    def run(
        self,
        command: str,
        workspace: str | Path,
        env: dict[str, str] | None = None,
    ) -> SandboxResult:
        # 将传入的工作目录转为绝对路径并校验是否存在，不合法直接抛出明确异常，避免后续 Docker 挂载失败。
        workspace_path = Path(workspace).resolve()
        if not workspace_path.is_dir():
            raise ValueError(f"workspace does not exist: {workspace_path}")

        # 步骤 2：生成唯一容器标识
        container_name = f"codeledger-{uuid.uuid4().hex[:12]}"


        #### 步骤 3：Docker 命令参数拼装
        # 这是整个沙箱安全机制的核心，所有安全限制、资源限制都通过这组参数落地。

        # 参数按功能可分为五类：
        # 1. **生命周期管理**：`--rm` 容器退出后自动删除，不残留僵尸容器；`--name` 指定名称，便于超时强制清理。
        # 2. **资源隔离**：CPU、内存、进程数三重硬限制，防止恶意代码耗尽宿主机资源。
        # 3. **权限限制**：丢弃所有 Linux 能力、禁止获取新权限、根文件系统只读，最大限度降低容器逃逸风险。
        # 4. **临时目录**：`/tmp` 使用内存文件系统，可写但不持久化，限制大小且禁止 setuid / 设备文件，满足测试写临时文件的需求又不留隐患。
        # 5. **工作目录**：指定非 root 用户运行，将宿主机目录挂载到容器内的 `/workspace` 并设为工作目录，代码修改实时同步。
        args = [
            self.docker_bin,
            "run",
            "--rm",
            "--name",
            container_name,
            "--network",
            self.config.network,
            "--cpus",
            self.config.cpus,
            "--memory",
            self.config.memory,
            "--pids-limit",
            str(self.config.pids_limit),
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=512m",
            "--user",
            "sandbox",
            "-v",
            f"{workspace_path}:/workspace:rw",
            "-w",
            "/workspace",
        ]

        # 步骤 4：环境变量注入
        # 遍历传入的环境变量字典，逐个用 `-e` 参数传入容器。用列表拼接而非字符串插值，避免 shell 注入风险。
        for key, value in (env or {}).items():
            args.extend(["-e", f"{key}={value}"])

        # 步骤 5：执行命令拼接
        # 指定镜像，通过 `bash -lc` 执行命令，保证加载 shell 配置、支持管道、重定向等完整 shell 语法。
        args.extend([self.config.image, "bash", "-lc", command])

        # 调用系统进程执行 Docker 命令：
        # - 捕获标准输出和错误输出，不直接打印到控制台
        # - 以文本模式返回输出，编码统一为 UTF-8
        # - 超时时间由配置控制，超时自动抛出异常
        try:
            proc = subprocess.run(
                args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.config.timeout_seconds,
            )
        # 步骤 7：超时异常处理
        # 超时触发后执行两个关键动作：

        # 1. **强制清理容器**：用 `docker rm -f` 强制停止并删除容器，避免超时后容器继续在后台运行造成资源泄漏。
        # 2. **返回标准化超时结果**：约定退出码 124 表示超时，输出通过 `_as_text` 做类型归一化，标记 `timed_out=True`。
        except subprocess.TimeoutExpired as exc:
            subprocess.run(
                [self.docker_bin, "rm", "-f", container_name],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            return SandboxResult(
                command=command,
                exit_code=124,
                stdout=_as_text(exc.stdout),
                stderr=_as_text(exc.stderr) or "sandbox timed out",
                timed_out=True,
                container_name=container_name,
            )
        # 步骤 8：正常返回
        # 命令正常执行完成，将进程返回的结果封装成 `SandboxResult` 对象返回
        return SandboxResult(
            command=command,
            exit_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            timed_out=False,
            container_name=container_name,
        )



## 五、端到端完整执行流程

# 以执行 `python --version` 为例，完整链路如下：

# 1. 实例化 `DockerSandbox()`，内部自动调用 `_resolve_docker_bin()` 定位到 docker.exe 路径
# 2. 调用 `run("python --version", "./target-repo")`
# 3. 校验 `./target-repo` 目录存在，转为绝对路径
# 4. 生成唯一容器名 `codeledger-a1b2c3d4e5f6`
# 5. 拼接完整的 docker run 参数数组，包含所有安全限制、资源配置、目录挂载
# 6. subprocess 启动 docker 进程，等待执行完成
# 7. 执行成功，封装为 `SandboxResult` 返回
# 8. 容器自动退出并删除，宿主机无残留

# ---

# ## 六、整体设计思想总结

# 1. **安全优先**：从用户权限、文件系统、网络、资源、系统能力五个维度构建多层防护，不可信代码的影响被严格限制在容器内。
# 2. **接口极简**：对外只暴露一个 `run` 方法，输入输出都是标准数据结构，上层完全不需要了解 Docker 知识。
# 3. **开箱即用**：三级降级自动查找 Docker 路径，覆盖绝大多数安装场景，零配置即可运行。
# 4. **契约稳定**：输入输出都是冻结数据类，结构稳定，作为跨模块交互的标准契约。
# 5. **容错兜底**：超时强制清理、类型兼容转换、路径多级兜底，多处设计都考虑了异常场景，避免资源泄漏和崩溃。
# 6. **向后兼容**：所有新增能力都不破坏原有接口，旧代码无需修改即可享受升级。