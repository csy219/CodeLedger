# 这是 **CodeLedger 单 Agent 闭环的首个感知组件：仓库地图（Repo Map）**，属于 W2-1 阶段的核心能力。
# 它的核心价值是解决「大仓库代码太多、塞不下模型上下文」的问题：不把所有源码丢给模型，而是生成一张轻量化的「仓库结构地图」
# —— 列出所有文件路径、每个 Python 文件的顶层类与函数名，让 Agent 先通过地图快速定位目标文件，再决定深入读取哪个文件的完整代码，大幅节省 token 并提升检索效率。


# 整个模块采用「数据模型 + 构建器 + 渲染输出」的三层架构：

# 1. **数据模型层**：`RepoFile` 描述单文件元数据，`RepoMap` 描述全仓库地图，均为不可变冻结类
# 2. **构建逻辑层**：`RepoMapBuilder` 负责递归扫描仓库、过滤无效目录、静态解析 Python 符号、生成结构化地图
# 3. **渲染输出层**：`RepoMap.render()` 将结构化数据转成大模型易读的纯文本格式


# 核心设计特点：

# - 纯静态解析：基于 Python 标准库 AST，只分析源码结构，不执行任何代码，绝对安全
# - 轻量化：只提取顶层类与函数，不展开嵌套结构，避免信息爆炸
# - 可管控：支持最大文件数、单文件最大符号数双重截断，避免超大仓库撑爆上下文
# - 过滤完备：复用项目统一的跳过目录规则，排除依赖缓存、构建产物等无意义文件



from __future__ import annotations

# `import ast`：导入 Python 标准库抽象语法树模块，是静态解析代码符号的核心，只做语法分析、不执行代码，安全无副作用
import ast
from dataclasses import dataclass
from pathlib import Path


# 段 2：全局过滤常量
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

# 段 3：单文件元数据类 RepoFile
# - `path: str`：文件在仓库内的相对路径，是地图的核心标识。
# - `symbols: tuple[str, ...] = ()`：文件内提取到的顶层符号（类、函数），用元组而非列表，天然不可变，符合 frozen 约束。
# - `size_bytes: int = 0`：文件字节大小，可辅助 Agent 判断文件大小、决定是否读取。
@dataclass(frozen=True)
class RepoFile:
    path: str
    symbols: tuple[str, ...] = ()
    size_bytes: int = 0


# 段 4：仓库地图类 RepoMap 与渲染方法
# - `root: str`：仓库根目录的绝对路径，标记地图对应的工作区。
# - `files: tuple[RepoFile, ...]`：所有文件的元数据列表，用元组保证不可变。
# - `truncated: bool = False`：是否被截断。当文件数超过上限时标记为 True，让 Agent 知道地图不完整，避免误以为仓库就只有这些文件。
@dataclass(frozen=True)
class RepoMap:
    root: str
    files: tuple[RepoFile, ...]
    truncated: bool = False

    # `render()` 方法：将结构化的地图数据渲染成纯文本，是给大模型看的最终输出格式。
    def render(self)->str:

        # 头部固定输出四行元信息：标题、根路径、文件总数、是否截断，让模型第一时间掌握地图全貌。
        lines=[
            "REPO MAP",
            f"root: {self.root}",
            f"files: {len(self.files)}",
            f"truncated: {self.truncated}",
            "",
        ]
        # 遍历每个文件：有符号的文件，路径后拼接所有符号名，用逗号分隔；无符号的非 Python 文件只输出路径。
        for file in self.files:
            if file.symbols:
                lines.append(f"{file.path}: {", ".join(file.symbols)}")
            else:
                lines.append(f"{file.path}")
        # 最终用换行符拼接成完整字符串，格式清晰、token 占用低，大模型极易解析
        return "\n".join(lines)

# 段 5：地图构建器构造函数
class RepoMapBuilder:

    # - `RepoMapBuilder` 是构建地图的核心类，采用建造者模式，配置与构建分离。
    # - `max_files: int = 200`：最大文件数限制，默认 200 个，超过就截断。防止超大仓库生成的地图过长，占用过多 token。
    # - `max_symbols_per_file: int = 20`：单文件最多提取的符号数，防止单文件符号过多撑爆地图。
    def __init__(
        self,
        max_files:int=200,
        max_symbols_per_file:int=20,
    )->None:
        self.max_files=max_files
        self.max_symbols_per_file=max_symbols_per_file

    # 段 6：核心构建方法 build
    def build(self,workspace:str | Path)->RepoMap:
        root=Path(workspace).resolve()
        files:list[RepoFile]=[]

        # `root.rglob("*")` 递归遍历工作目录下的所有路径，`sorted()` 按路径排序，保证生成的地图顺序稳定、可复现。
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if any(part in SKIP_DIRS for part in path.parts):
                continue

            relative=path.relative_to(root).as_posix()
            symbols=self._python_symbols(path)
            files.append(
                RepoFile(
                    path=relative,
                    symbols=symbols,
                    size_bytes=path.stat().st_size,
                )
            )

        truncated=len(files)>self.max_files
        return RepoMap(
            root=str(root),
            files=tuple(files[:self.max_files]),
            truncated=truncated,
        )

    # 段 7：Python 符号提取私有方法
    def _python_symbols(self,path:Path)->tuple[str,...]:
        # 先判断文件后缀，非 `.py` 文件直接返回空元组，不做解析。
        if path.suffix !=".py":
            return ()

        # - 读取文件内容，用 `ast.parse()` 解析成抽象语法树。整个过程只做语法分析，不执行代码，绝对安全，不会触发文件里的任何逻辑。
        # - 捕获语法错误，语法不合法的 Python 文件直接返回空，不中断整个地图构建
        try:
            tree=ast.parse(path.read_text(encoding="utf-8",errors="replace"))
        except SyntaxError:
            return ()
        
        # - 只遍历语法树的**顶层节点**，不深入类和函数内部。
        # - 识别顶层函数、异步函数，格式化为 `def 函数名`；识别顶层类，格式化为 `class 类名`。
        # - 达到单文件最大符号数就停止遍历，避免信息过载。

        # > 设计考量：只取顶层符号是刻意为之。如果把嵌套函数、方法全部列出来，地图会非常冗长，反而干扰模型判断；顶层结构足以帮助模型定位文件，细节可以后续调用 `read_file` 深入查看。
        symbols:list[str]=[]
        for node in tree.body:
            if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
                symbols.append(f"def {node.name}")
            if isinstance(node,ast.ClassDef):
                symbols.append(f"class {node.name}")
            if len(symbols)>=self.max_symbols_per_file:
                break

        return tuple(symbols)
        


