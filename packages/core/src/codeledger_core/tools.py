# 代码整体定位

# 这是 **CdeLedger 项目的 Agent 工具核心层**，是智能体操作代码仓库的标准工具集。
# 它向下封装了文件系统和 Docker 沙箱，向上为 Agent 提供**格式统一、安全受控、可扩展**的工具调用接口。
# 所有对代码的查看、修改、测试操作都必须通过这层工具完成，不能直接操作底层文件和沙箱。

# 整体调用链路：
# `Agent → ToolRegistry(工具分发) → 具体工具函数 → 本地文件系统(受控读写) / Docker沙箱(执行测试)`
from __future__ import annotations

# 第 2 行：导入 Python 标准库的差异对比模块，用于生成标准 unified diff 格式的代码变更对比，让补丁修改结果可视化。
import difflib
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol


# - `MAX_FILE_BYTES`：单文件最大读取 1MB，防止大文件拖慢性能、避免读取二进制大文件。
# - `SKIP_DIRS`：搜索 / 遍历时自动跳过的目录，都是依赖缓存、构建产物、版本控制目录，无业务代码且数量庞大，跳过可大幅提升搜索速度，避免无关干扰。
MAX_FILE_BYTES = 1_000_000

SKIP_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "dist",
    "build",
}

# 2. SandboxLike 协议（接口抽象）
# 这是 Python 的**结构子类型协议**，用来定义沙箱的接口规范：只要实现了 `run` 方法，就符合沙箱标准。

# - 设计目的：**解耦工具层和具体沙箱实现**。工具层不直接依赖 `DockerSandbox` 类，只依赖这个抽象接口，方便替换沙箱实现、单元测试时用 Mock 沙箱。
class SandboxLike(Protocol):
    def run(self,command:str,workspace:str | Path)->Any:
        ...


# 3. ToolError 异常
# 工具层专属的运行时异常，用来区分「工具业务错误」和「系统异常」，比如路径越界、文件不存在、参数非法都抛出这个异常。
class ToolError(RuntimeError):
    """Raised when a tool cannot complete safely."""



## 二、标准数据结构
# 所有工具都使用统一的返回格式，上层 Agent 不用为每个工具单独做解析逻辑。

# 1. SearchMatch — 搜索匹配项
# 单条代码搜索结果，记录匹配的文件路径、行号、匹配行内容，冻结不可变。
@dataclass(frozen=True)
class SearchMatch:
    path:str
    line:int
    text:str


# 2. ToolResult — 工具统一返回值
# **所有工具的标准返回结构**，是工具层的核心数据契约：

# - `ok`：布尔值，工具是否执行成功
# - `output`：成功时的主输出文本
# - `error`：失败时的错误信息
# - `metadata`：附加结构化数据，比如匹配列表、行号信息、退出码等
# > 设计价值：上层 Agent 处理所有工具都只用同一套逻辑：判断 `ok`，读 `output`，出错读 `error`，极大简化交互逻辑。

@dataclass(frozen=True)
class ToolResult:
    ok:bool
    output:str=""
    error:str | None=None
    metadata:dict[str,Any]=field(default_factory=dict)



# 三、核心安全底座：_safe_path 路径穿越防护
# 这是**文件操作的安全闸门**，所有涉及文件路径的工具都必须经过它校验，核心作用是**防止路径穿越攻击**。
### 工作原理
# 1. 把工作目录和传入的相对路径都解析成绝对路径
# 2. 校验最终路径必须在工作目录内部
# 3. 一旦发现 `../` 这类跳出工作目录的路径，直接抛出 `ToolError` 拒绝
# > 典型防御场景：Agent 传入 `../../etc/passwd` 试图读取系统文件，会被直接拦截，保证所有文件操作都严格限制在代码仓库范围内。
def _safe_path(workspace:str | Path,relative_path:str)->Path:
    root=Path(workspace).resolve()
    candidate=(root / relative_path).resolve()
    if candidate != root and root not in candidate.parents:
        raise ToolError(f"path escapes workspace: {relative_path}")
    return candidate


# 四、五大核心工具函数


# 1. search_code — 代码全文搜索
# **作用**：在工作目录内递归搜索包含指定字符串的代码行，类似轻量版 grep。
# ### 执行逻辑
# 1. 校验查询关键词非空
# 2. 编译正则，支持大小写不敏感
# 3. 递归遍历文件，自动跳过 `SKIP_DIRS` 目录
# 4. 跳过超过 1MB 的大文件
# 5. 逐行匹配，命中就记录路径、行号、内容
# 6. 达到最大结果数立即停止，避免无限遍历

# ### 设计要点
# - 限制最大结果数，防止返回过多内容淹没 Agent 上下文
# - 匹配行内容截断 300 字符，避免超长行
# - 错误自动跳过，不会因为单个文件读取失败导致整个搜索崩溃
def search_code(workspace,query,max_results=20,case_sensitive=False)->ToolResult:
    if not query:
        raise ToolError("query must not be empty")

    root=Path(workspace).resolve()
    # 设置正则标志位：区分大小写则无标志，不区分则加入忽略大小写标志。
    flags=0 if case_sensitive else re.IGNORECASE

    # 编译正则表达式。使用 `re.escape()` 转义关键词中的所有特殊字符，保证搜索的是纯文本内容，不会被当作正则语法解析，避免注入。
    pattern=re.compile(re.escape(query),flags)
    matches:list[SearchMatch]=[]

    # 递归扫描仓库文件。
    for path in root.rglob("*"):
        if len(matches) >= max_results:
            break
        if not path.is_file() or any(part in SKIP_DIRS for part in path.parts):
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            text=path.read_text(encoding="utf-8",errors="replace")
        # 捕获所有系统 IO 错误（权限不足、二进制文件损坏等），直接跳过该文件。
        except OSError:
            continue

        for line_number,line in enumerate(text.splitlines(),start=1):
            if pattern.search(line):
                matches.append(
                    SearchMatch(
                        path=str(path.relative_to(root)),
                        line=line_number,
                        text=line[:300],
                    )
                )
                if len(matches) >= max_results:
                    break
    return ToolResult(
        ok=True,
        output=f"found {len(matches)} matches",
        metadata={"matches":[asdict(match) for match in matches]}
    )

# 2. read_file — 带行号文件读取
#### 段作用
# 读取指定文件的内容，支持按行号范围读取，输出自动带行号，方便 Agent 精确定位代码位置。
def read_file(workspace:str | Path,path:str,start_line:int=1,end_line:int | None=None)->ToolResult:
    if start_line<1:
        raise ToolError(f"file does not exist: {path}")

    target=_safe_path(workspace,path)
    if not target.is_file():
        raise ToolError(f"file does not exist: {path}")

    lines=target.read_text(encoding="utf-8",errors="replace").splitlines()
    last_line=end_line or len(lines)
    if last_line < start_line:
        raise ToolError("end_line must be >= start_line")

    selected=lines[start_line-1:last_line]
    # - `line_number:>4`：行号右对齐，占 4 个字符宽度，整齐美观
    # - 每行格式为「行号：内容」
    # - 行号从实际的起始行开始计数，不是从 1 开始
    numbered="/n".join(
        f"{line_number:>4}: {line}"
        for line_number,line in enumerate(selected,start=start_line)
    )

    return ToolResult(
        ok=True,
        output=numbered,
        metadata={
            "path": path,
            "start_line": start_line,
            "end_line": last_line,
            "total_lines": len(lines),
        },
    )

# 3. apply_patch — 精确单处代码修改
# 精确修改文件中的某一段代码，是 Agent 修改代码的核心执行工具。
def apply_patch(workspace,path,old_text,new_text)->ToolResult:
    if not old_text:
        raise ToolError("old_text must not be empty")

    target = _safe_path(workspace, path)
    if not target.is_file():
        raise ToolError(f"file does not exist: {path}")
    # 读取原文件完整内容。
    original=target.read_text(encoding="utf-8")
    # 统计旧文本在文件中出现的次数。
    occurrences=original.count(old_text)
    # 第 129-132 行：核心校验：强制要求旧文本必须恰好出现 1 次。0 次说明匹配不上，多次说明位置不唯一，两种情况都拒绝执行。
    # > 设计目的：保证每次修改都是精准、唯一的，防止出现替换错位置、批量误替换的问题。
    if occurrences !=1:
        raise ToolError(
            f"expected exactly one occurrence of old_text, found {occurrences}"
        )

    #执行替换，第三个参数 `1` 表示只替换第 1 处匹配。
    updated=original.replace(old_text,new_text,1)

    # 生成标准 unified diff 格式的变更对比：
    # - `keepends=True` 保留换行符，保证 diff 格式准确
    # - 指定新旧文件名前缀，符合 git diff 的显示习惯
    # - `"".join()` 把生成的行列表拼接成完整字符串
    diff="".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            updated.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )

    # 把修改后的内容写回文件。
    target.write_text(updated,encoding="utf-8")
    # 返回结果，output 是 diff 对比文本，方便 Agent 校验修改是否正确。
    return ToolResult(
        ok=True,
        output=diff,
        metadata={
            "path":path,
            "old_occurrences":occurrences,
            "changed":True,
        }
    )

# 4. git_diff — 全局变更查看
# 查看当前工作区所有未提交的代码变更，让 Agent 可以全局校验自己的修改。
# - 直接调用宿主机的 git 命令，不依赖容器内的 git，保证和仓库状态一致。
# - 加入 `--no-ext-diff` 禁用外部对比工具，保证输出格式稳定统一。
# - 执行失败也会封装成标准错误结果，不会抛出异常。
def git_diff(workspace)->ToolResult:
    root=Path(workspace).resolve()
    # 调用系统 git 命令执行 diff：
    # - `--no-ext-diff`：禁用外部 diff 工具，保证输出格式稳定
    # - `--`：选项结束符，防止路径名以横杠开头被当作选项
    # - `cwd=root`：在工作目录下执行命令
    # - 捕获输出、文本模式、UTF-8 编码、错误替换
    proc = subprocess.run(
        ["git", "diff", "--no-ext-diff", "--"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    # 命令执行失败（退出码非 0），返回标准错误结果，附带错误信息和退出码。
    if proc.returncode != 0:
        return ToolResult(
            ok=False,
            error=proc.stderr.strip() or "git diff failed",
            metadata={"returncode": proc.returncode},
        )
    return ToolResult(
        ok=True,
        output=proc.stdout,
        metadata={"returncode": proc.returncode},
    )


# 5. run_tests — 沙箱内执行测试
#### 段作用
# 在 Docker 沙箱中执行测试命令，是所有代码运行的唯一出口，所有执行类操作都通过它进入隔离环境。
#### 核心代码分析

# - 它不直接调用系统命令，而是传入沙箱对象去执行，命令会先经过命令白名单策略校验，再在隔离容器中运行，安全有完整保障。
# - 成功条件做了双重判断：退出码为 0 且没有触发超时，避免超时被误判为失败。
# - 元数据里附带完整的执行信息，方便上层排查问题。
def run_tests(sandbox:SandboxLike,workspace:str | Path,command:str="pytest -q")->ToolResult:
    result=sandbox.run(command,workspace)
    ok=result.exit_code==0 and not result.timed_out

    return ToolResult(
        ok=ok,
        output=result.stdout,
        error=None if ok else result.stderr,
        metadata={
            "command": command,
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "container_name": result.container_name,
        },
    )


# 六、工具注册调度器：ToolRegistry
# 这是整个工具层的统一入口，Agent 不直接调用各个工具函数，而是通过这个注册器按名称调用，是典型的注册表模式。
### 核心代码分析

# - 初始化时用字典注册所有工具，提前绑定好工作目录和沙箱上下文。上层调用时只需要传工具名和业务参数，不用重复传上下文。
# - 扩展性很好，新增工具只需要写一个函数、在注册字典里加一行，调度逻辑完全复用。
class ToolRegistry:
    def __init__(self,workspace:str | Path,sandbox:SandboxLike)->None:
        self.workspace=Path(workspace).resolve()
        self.sandbox=sandbox
        self._handlers = {
            "search_code": lambda **kwargs: search_code(self.workspace, **kwargs),
            "read_file": lambda **kwargs: read_file(self.workspace, **kwargs),
            "apply_patch": lambda **kwargs: apply_patch(self.workspace, **kwargs),
            "git_diff": lambda **kwargs: git_diff(self.workspace),
            "run_tests": lambda **kwargs: run_tests(
                self.sandbox,
                self.workspace,
                **kwargs,
            ),
        }
    # - `call` 方法是统一调度入口：未知工具直接返回标准错误，所有异常都被捕获并封装成错误结果。上层 Agent 永远不会收到异常，只用判断 `ok` 字段即可，鲁棒性极强。
    def call(self,name:str,**kwargs:Any)->ToolResult:
        handler=self._handlers.get(name)
        if handler is None:
            return ToolResult(ok=False,error=f"unknown tool: {name}")

        try:
            return handler(**kwargs)
        except Exception as exc:
            return ToolResult(ok=False,error=str(exc))

    
__all__ = [
    "SandboxLike",
    "SearchMatch",
    "ToolError",
    "ToolRegistry",
    "ToolResult",
    "apply_patch",
    "git_diff",
    "read_file",
    "run_tests",
    "search_code",
]







    







