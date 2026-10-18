### 文件 1：Dockerfile
# **整体定位**：沙箱镜像的构建说明书。所有沙箱容器都基于同一个镜像启动，保证执行环境的一致性、可复现性。

# 段 1：基础镜像与 Python 运行时配置
# - 选定官方精简版 Python 3.11 作为基础，`slim` 变体移除了大量非必要系统组件，镜像体积小、攻击面小。
# - 选择 3.11 而非更新版本：SWE-bench 等主流代码评测基准的目标仓库普遍兼容 3.11，更贴近真实生产项目环境；同时沙箱与宿主机 Python 版本解耦，评测结果不受本地环境影响。
# - 两个环境变量：
#   - `PYTHONDONTWRITEBYTECODE=1`：禁止生成 `.pyc` 字节码缓存，避免污染挂载的工作目录。
#   - `PYTHONUNBUFFERED=1`：关闭 Python 标准输出缓冲区，日志实时输出，方便 Agent 流式获取执行结果。
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1


# 段 2：系统依赖安装
# - 单条 RUN 指令完成更新、安装、清理，减少镜像层数，控制体积。
# - `--no-install-recommends`：只安装强制依赖，不安装推荐的可选包，进一步压缩镜像大小和攻击面。
# - 安装的工具都是代码执行的最小必要集：
#   - `bash`：命令执行外壳
#   - `git`：代码仓库拉取、diff、提交操作
#   - `curl`：网络调试、文件下载
#   - `ca-certificates`：SSL 证书，保证 HTTPS 可用
#   - `build-essential`：gcc/make 等编译工具，安装含 C 扩展的 Python 依赖时需要
# - 最后删除 apt 缓存，是 Docker 镜像优化的标准实践。
RUN apt-get update && apt-get install -y --no-install-recommends \
        bash \
        git \
        curl \
        ca-certificates \
        build-essential \
    && rm -rf /var/lib/apt/lists/*



# 段 3：非 root 用户与工作目录创建
# - 创建专用普通用户 `sandbox`，固定 UID 10001，**禁止使用 root 用户运行容器内进程**。
#   - 安全意义：即使容器发生逃逸，攻击者在宿主机上也只有普通用户权限，无法破坏系统；同时防止 Agent 在容器内修改系统文件、安装后门、提权。
# - 创建 `/workspace` 作为工作目录，所有权交给 sandbox 用户，用于挂载宿主机的代码目录。
RUN useradd --create-home --uid 10001 --shell /bin/bash sandbox \
    && mkdir -p /workspace \
    && chown -R sandbox:sandbox /workspace


# 段 4：运行时默认配置
# - `USER sandbox`：容器启动后默认以 sandbox 用户身份执行所有命令，从根源上限制权限。
# - `WORKDIR /workspace`：默认工作目录指向挂载点，执行命令时无需额外 cd。
# - `CMD ["bash"]`：默认启动交互式 bash，无命令时进入外壳。
USER sandbox
WORKDIR /workspace

CMD ["bash"]