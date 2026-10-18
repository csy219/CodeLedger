# 这是 **CodeLedger 沙箱的命令安全策略引擎**，属于沙箱的**前置安全网关**。所有要送入 Docker 容器执行的命令，
# 都必须先经过这层校验，拦截危险命令、非法操作和绕过隔离的尝试，是继 Docker 容器本身隔离之后的**第二道安全防线**。

# 核心设计原则：**默认拒绝，仅放行已知安全的操作**（白名单机制），从命令格式、操作符、可执行程序、子命令、参数特征
# 多个维度逐层拦截，最大限度限制 Agent 的操作权限，只保留代码测试、代码查看、构建所必需的最小命令集。


# 依赖	作用
# re	正则表达式引擎，用于匹配危险命令的特征模式。
# shlex	核心依赖，Shell 词法解析器。按 POSIX 规则正确拆分命令行，处理引号、转义，准确识别可执行程序、参数、操作符，避免简单字符串分割的误判和绕过。
# PurePosixPath	POSIX 路径处理，用来提取命令中的纯可执行文件名（去掉路径前缀），防止通过 /usr/bin/rm 这类完整路径绕过黑名单。
# dataclass	快速定义检查结果的数据结构。
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import PurePosixPath


## 二、安全规则常量（五层规则库）
# 一共定义了五组规则常量，按检查优先级从高到低排列，构成多层拦截体系。


# 1. SHELL_OPERATORS — Shell 操作符黑名单
# **作用：禁止所有 Shell 流程控制、管道、重定向操作符。**

# 为什么必须禁止：

# - 防止命令拼接绕过：比如 `python && rm -rf /`，如果只检查第一个命令 `python` 就会放行，后面拼接的危险命令就绕过了白名单。
# - 防止重定向篡改：比如 `echo 内容 > 重要文件`，通过重定向修改系统文件。
# - 防止管道注入：比如 `正常命令 | 危险命令`。
# > 设计逻辑：沙箱只允许执行**单条命令**，不允许任何复合命令，从语法层面堵死拼接绕过的可能。
SHELL_OPERATORS = {
    ";",
    "&&",
    "||",
    "|",
    "&",
    ">",
    ">>",
    "<",
    "2>",
    "2>>",
}



# 2. ALLOWED_EXECUTABLES — 可执行程序白名单
# **作用：明确列出所有允许执行的命令，不在列表里的一律拒绝。**

# 这是整个策略的核心原则：**默认拒绝，只允许已知安全的命令**。列表里全是代码开发相关的工具：

# - 代码运行：`python`、`python3`
# - 测试与检查：`pytest`、`ruff`、`mypy`、`coverage`
# - 代码查看：`ls`、`cat`、`head`、`tail`、`grep`、`rg`、`find`
# - 文本处理：`sed`、`awk`
# - 版本控制：`git`
# - 构建工具：`make`、`npm`
# - 基础工具：`pwd`、`echo`、`test`、`printf`
# > 白名单远比黑名单安全：黑名单是「知道什么危险就拦什么」，总有漏网之鱼；白名单是「只允许知道安全的」，默认所有新命令都是危险的。
ALLOWED_EXECUTABLES = {
    "python",
    "python3",
    "pytest",
    "git",
    "ls",
    "cat",
    "head",
    "tail",
    "grep",
    "rg",
    "find",
    "sed",
    "awk",
    "make",
    "npm",
    "ruff",
    "mypy",
    "coverage",
    "pwd",
    "echo",
    "true",
    "false",
    "test",
    "printf",
}



# 3. READ_ONLY_GIT_SUBCOMMANDS — Git 只读子命令白名单
# **作用：git 命令只允许只读操作，禁止任何修改仓库的操作。**

# 允许的都是查看状态、看差异、看日志、列文件这类只读操作；隐含禁止 `push`、`commit`、`reset --hard`、`clean -f`、`checkout` 等会修改代码、提交代码、回滚代码的操作。
# > 设计目的：防止 Agent 乱提交代码、强制回滚代码、清空工作区，只允许它查看代码信息。
READ_ONLY_GIT_SUBCOMMANDS = {
    "status",
    "diff",
    "log",
    "show",
    "rev-parse",
    "ls-files",
    "grep",
    "blame",
}


# 4. DANGEROUS_EXECUTABLES — 危险程序黑名单
# **作用：第二层兜底拦截，明确禁止高风险命令。**

# 覆盖几类高危操作：

# - 文件破坏：`rm`、`dd`、`mkfs`
# - 权限提升：`sudo`、`su`、`chmod`、`chown`
# - 网络通信：`curl`、`wget`、`ssh`、`scp`、`nc`
# - 容器逃逸：`docker`、`kubectl`
# - 系统控制：`systemctl`、`shutdown`、`crontab`、`iptables`
# - Shell 绕过：`bash`、`sh`、`xargs`（防止启动新 shell 绕过白名单）
# - 其他脚本解释器：`perl`、`ruby`（防止用其他解释器执行危险脚本）
# > 虽然有白名单本来就会拒绝这些，但黑名单作为第二层冗余防护，同时也更清晰地标记出高危命令。
DANGEROUS_EXECUTABLES = {
    "rm",
    "dd",
    "mkfs",
    "shutdown",
    "reboot",
    "poweroff",
    "sudo",
    "su",
    "mount",
    "umount",
    "chmod",
    "chown",
    "kill",
    "pkill",
    "curl",
    "wget",
    "ssh",
    "scp",
    "nc",
    "ncat",
    "socat",
    "docker",
    "kubectl",
    "systemctl",
    "service",
    "crontab",
    "iptables",
    "nft",
    "bash",
    "sh",
    "zsh",
    "fish",
    "env",
    "xargs",
    "perl",
    "ruby",
}


# 5. DANGEROUS_PATTERNS — 危险特征正则库
# **作用：专门拦截伪装、拼接、变形的危险命令，是白名单的补充。**

# 针对那些「命令本身合法，但参数用法危险」的场景，比如：

# - `rm -rf /`、`rm -rf ~`：递归删除根目录 / 家目录
# - `:(){ :|:& };:`：fork 炸弹，耗尽系统进程
# - `git reset --hard`、`git clean -f`：强制重置 / 清空代码
# - `chmod 777 /`：全开文件权限
# - `find -delete`、`find -exec`：通过 find 批量删除 / 执行命令
# - `pip install`、`npm install`：安装外部依赖，可能引入恶意包
# > 很多危险操作是合法命令的危险参数，白名单不能直接禁整个命令（比如 `find` 本身是常用工具），就用正则匹配危险参数组合。
DANGEROUS_PATTERNS = [
    re.compile(r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f?\s+(/|\*|~)"),
    re.compile(r"\bdd\s+if="),
    re.compile(r"\bmkfs(\.\w+)?\b"),
    re.compile(r"\b(shutdown|reboot|poweroff|halt)\b"),
    re.compile(r":\(\)\s*\{"),
    re.compile(r"\bchmod\s+777\s+/"),
    re.compile(r"\b(chown|chgrp)\s+.*\s+/"),
    re.compile(r"\bgit\s+(push|reset\s+--hard|clean\s+-[a-zA-Z]*f)"),
    re.compile(r"\b(find\s+.*\s+-delete|\bexec\s*\(|\bpopen\s*\()"),
    re.compile(r"\b(pip|pip3)\s+install\b"),
    re.compile(r"\b(npm|pnpm|yarn)\s+(install|add|publish)\b"),
]


## 三、数据结构与异常

### 1. CommandDecision — 检查结果
# 冻结数据类，封装校验结果：
# - `allowed`：是否允许执行
# - `reason`：拒绝原因，用于日志和错误提示
# - `executable`：识别出的可执行程序名，供上层使用
@dataclass(frozen=True)
class CommandDecision:
    allowed:bool
    reason:str=""
    executable: str | None =None


# 2. CommandRejected — 拒绝异常
class CommandRejected(ValueError):
    """Raised when a command violates the sandbox policy."""



# 四、核心校验逻辑：CommandPolicy
class CommandPolicy:
    ### 1. `check()` 方法：完整校验流程
    # 输入命令字符串，输出校验结果。整个检查流程**按风险从高到低、检查成本从低到高排序**，先粗筛再细查，效率最优。
    def check(self,command:str) -> CommandDecision:

        #### 第 1 步：空命令校验
        # 空字符串、全空格命令直接拒绝。
        if not command.strip():
            return CommandDecision(False,"empty command")

        #### 第 2 步：危险模式正则粗筛
        # 先用正则快速扫描整个命令字符串，命中任何危险特征直接拒绝。
        # 放在最前面：正则匹配快，明显的危险命令直接拦掉，不用走后面的解析流程。
        for pattern in DANGEROUS_PATTERNS:
            if pattern.search(command):
                return CommandDecision(False, f"dangerous pattern: {pattern.pattern}")

        #### 第 3 步：命令词法解析
        # 用 `shlex` 按 POSIX 规则正确拆分命令，处理引号、转义、空格，得到准确的 token 列表。
        # - 解析失败直接拒绝，防止畸形命令绕过检查。
        # - 不用简单的 `split()`：比如 `echo "hello world"` 用 split 会错误拆成三份，shlex 才会正确拆成两份。
        try:
            lexer=shlex.shlex(command,posix=True,punctuation_chars=True)
            lexer.whitespace_split=True
            tokens=list(lexer)
        except ValueError as exc:
            return CommandDecision(False,f"cannot parse command: {exc}")

        if not tokens:
            return CommandDecision(False,"empty command")

        #### 第 4 步：Shell 操作符拦截
        # 检查 token 列表里有没有 shell 操作符，有就直接拒绝。
        # > 防止通过操作符拼接多条命令绕过白名单。
        operator=next((token for token in tokens if token in SHELL_OPERATORS),None)
        if operator:
            return CommandDecision(False,f"shell operator is not allowed: {operator}")

        #### 第 5 步：可执行程序提取与黑名单校验
        # 提取第一个 token 的纯程序名（去掉路径、转成小写），先过黑名单。
        # - 用 `PurePosixPath(tokens[0]).name`：比如 `/bin/rm` 会提取出 `rm`，防止通过完整路径绕过黑名单。
        executable =PurePosixPath(tokens[0]).name.lower()

        if executable in DANGEROUS_EXECUTABLES:
            return CommandDecision(False, f"executable is blocked: {executable}")


        #### 第 6 步：可执行程序白名单校验
        # 不在白名单里的一律拒绝，这是核心门禁。
        if executable not in ALLOWED_EXECUTABLES:
            return CommandDecision(False, f"executable is not allowlisted: {executable}")


        #### 第 7 步：各命令专项参数校验
        # 通过了命令名检查，还要针对特定命令检查参数，防止合法命令的危险用法：
        # 命令	校验规则	目的
        # git	必须有子命令，且子命令必须在只读白名单里	禁止 git 修改、提交操作
        # pip/pip3	直接全部禁止	防止安装外部依赖
        # sed	禁止 -i 参数	禁止原地修改文件，sed 只能用来查看、管道处理
        # find	禁止 -delete、-exec、-execdir 参数	防止通过 find 批量删除、执行命令
        # npm	只允许 npm test、npm run	禁止 npm install、npm publish 等安装 / 发布操作
        # python	禁止 python -m pip、python -m ensurepip	防止通过 python 模块调用 pip 绕过禁令
        if executable == "git":
            if len(tokens) < 2:
                return CommandDecision(False, "git subcommand is required")
            subcommand = tokens[1].lower()
            if subcommand not in READ_ONLY_GIT_SUBCOMMANDS:
                return CommandDecision(False, f"git subcommand is not allowed: {subcommand}")

        if executable in {"pip", "pip3"}:
            return CommandDecision(False, "pip is not allowed inside the sandbox")

        if executable == "sed" and "-i" in tokens:
            return CommandDecision(False, "sed -i is not allowed")

        if executable == "find":
            if any(token in {"-delete", "-exec", "-execdir"} for token in tokens):
                return CommandDecision(False, "find delete/exec is not allowed")

        if executable == "npm":
            if len(tokens) < 2 or tokens[1] not in {"test", "run"}:
                return CommandDecision(False, "only npm test / npm run are allowed")

        if executable in {"python", "python3"}:
            if len(tokens) >= 3 and tokens[1] == "-m" and tokens[2] in {"pip", "ensurepip"}:
                return CommandDecision(False, "python -m pip is not allowed")

        # 8 步：全部通过，放行
        return CommandDecision(True,executable=executable)

    # 2. `assert_allowed()` 方法：断言式便捷接口
    # 便捷方法：不返回结果，不通过直接抛异常。适合上层直接调用，不用自己判断处理。
    def assert_allowed(self,command:str)->None:
        decision=self.check(command)
        if not decision.allowed:
            raise CommandRejected(decision.reason)




## 五、整体设计特点

# 1. **白名单优先，默认拒绝**：最核心的安全原则，只放行明确验证过安全的命令，远优于黑名单机制。
# 2. **多层纵深防御**：正则模式层 → 操作符层 → 命令名黑名单 → 命令名白名单 → 参数专项校验，五层拦截，一层漏了还有下一层。
# 3. **最小权限原则**：只给 Agent 完成代码测试、查看所必需的最少命令，多余权限一律不给。
# 4. **防绕过设计**：shlex 正确解析、路径提取纯程序名、禁止操作符拼接，多种手段防止绕过白名单。
# 5. **性能友好**：检查成本低的在前，成本高的在后，快速拒绝明显危险的命令。

# ---

# ## 六、结合沙箱的完整安全链路

# 到这里，整个沙箱的安全体系就完整了，从外到内四层防护：

# 1. **命令策略层**：就是这段代码，命令执行前先审计，危险命令直接拦在门外
# 2. **容器隔离层**：Docker 容器，进程、文件系统、网络天然隔离
# 3. **资源限制层**：CPU、内存、进程数硬限制，防止耗尽资源
# 4. **文件系统层**：根目录只读，只有工作目录可写，/tmp 用临时文件系统

# ---

# ## 七、举例演示

# ### ✅ 允许通过的命令

# ```
# policy = CommandPolicy()
# policy.check("pytest tests/")  # 通过
# policy.check("git diff")       # 通过，只读子命令
# policy.check("cat main.py")    # 通过
# policy.check("python test.py") # 通过
# ```

# ### ❌ 被拒绝的命令

# ```
# policy.check("rm -rf /")                # 危险模式正则拦截
# policy.check("python && rm -rf /")      # shell 操作符拦截
# policy.check("sudo apt install")        # 黑名单拦截
# policy.check("npm install requests")    # 专项规则拦截
# policy.check("git push origin main")    # git 子命令拦截
# policy.check("sed -i 's/a/b/' file.py") # sed -i 原地修改拦截
# ```