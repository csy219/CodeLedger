# RepoMapBuilder 代码中所有 `path` 相关用法详解

> 
> 这里的 `path` 全部是 **`pathlib.Path` 对象**（Python3.4+ 内置面向对象路径库，替代旧版 `os.path`），不是字符串。
> 导入：`from pathlib import Path`

先把代码里出现的 path API 全部列出来，逐个解析底层逻辑 + 示例：

```
Path(workspace).resolve()          # 构造Path + resolve
root.rglob("*")                    # rglob递归glob遍历
path.is_file()                     # 判断是否文件
path.parts                         # 路径分段元组
path.relative_to(root)             # 求相对路径
path.stat()                        # 获取文件系统stat信息
path.stat().st_size                # 文件字节大小
path.suffix                        # 文件后缀名
path.read_text(encoding="utf-8", errors="replace") #读取文本
```

---

## 1. `Path(workspace)` —— Path 类构造函数

### 作用

把字符串路径 / 另一个 Path 对象包装成 `Path` 实例。

### 底层

`pathlib.Path` 会自动区分 Windows / POSIX（linux/mac）路径，自动处理 `/` 和 `\`，内部保存路径分段，**不会立刻访问磁盘**，只是内存里的路径对象。

> 
> Path 对象是惰性的：构造的时候不会去磁盘检查文件是否存在，只有调用 `is_file()/stat()/read_text()` 这类方法才触发 IO。

示例

```
from pathlib import Path
p = Path("/home/code/repo")
print(type(p)) # <class 'pathlib.PosixPath'> linux；Windows下是WindowsPath
```

## 2. `.resolve()`

代码：`Path(workspace).resolve()`

### 作用

返回**绝对路径**，解析软链接、`../`、`./`，得到完整真实路径。

### 底层

调用操作系统系统调用：`os.path.realpath()`。

- 把相对路径转绝对路径
- 展开符号链接 (软链接)
- 消除 `.` `..`
- **会访问磁盘**（必须读取文件系统）

示例

```
# 当前目录 /home/code
p = Path("./src").resolve()
print(p) # /home/code/src
```

> 
> ⚠️ 如果路径不存在，resolve () 在部分 python 版本也能解析，但软链接解析失败会抛异常。

## 3. `root.rglob("*")`

### 作用

递归遍历目录，匹配 glob 模式，遍历**所有子目录**下的文件。
`rglob(pattern)` = `glob("**/" + pattern)`

### 底层

底层调用操作系统目录遍历（`os.scandir`），迭代器惰性产出 Path 对象，**不是一次性加载全部文件到内存**。
`rglob` 返回生成器，循环 `for path in root.rglob("*")` 逐个产生子文件 / 文件夹 Path 实例。

示例

```
root = Path("./demo")
for p in root.rglob("*"):
    print(p)
# 递归输出demo下所有文件+子文件夹
```

> 
> 对比：`glob("*")` 只遍历当前目录，不进子文件夹；`rglob` 递归所有子目录。

## 4. `path.is_file()`

### 作用

判断当前 path 是否是**普通文件**，返回 bool；文件夹返回 False。

### 底层

系统调用 `os.stat()`，读取文件元信息，判断文件类型。

> 
> 会访问磁盘 IO；如果文件已经被删除（遍历过程中文件消失）会抛 `FileNotFoundError`。

示例

```
p = Path("test.py")
if p.is_file():
    print("这是文件")
```

## 5. `path.parts`

代码：`any(part in SKIP_DIRS for part in path.parts)`

### 作用

把路径分割成元组，每一段是目录 / 文件名。**纯字符串分割，不访问磁盘**。

- POSIX 示例：`/a/b/c/file.py` → `('/', 'a', 'b', 'c', 'file.py')`
- Windows 示例：`C:\code\test.py` → `('C:\\', 'code', 'test.py')`

示例

```
p = Path("/repo/src/utils.py")
print(p.parts)
# ('/', 'repo', 'src', 'utils.py')
```

> 
> 代码逻辑：只要路径任意一段目录名在 SKIP_DIRS（如`.git`、`__pycache__`），直接跳过该文件。

## 6. `path.relative_to(root)`

代码：`path.relative_to(root)`

### 作用

计算 `path` 相对于 `root` 的相对路径，返回新 Path 对象。

> 
> 前提：path 必须在 root 目录下，否则抛 `ValueError`。

### 底层

路径字符串分段计算，**不访问磁盘**。

示例

```
root = Path("/repo")
full_path = Path("/repo/src/main.py")
rel = full_path.relative_to(root)
print(rel) # src/main.py
str(rel)   # 转字符串存入RepoFile.path
```

## 7. `path.stat()`

### 作用

获取文件系统元数据，返回 `os.stat_result` 对象。**磁盘 IO 调用**。
底层：调用系统 `stat()` 系统调用，拿到文件元信息：大小、修改时间、权限等。

## 8. `path.stat().st_size`

`stat_result` 的属性：`st_size` → 文件字节数。

- 单位：bytes
- 底层：stat 系统调用返回的字段，不需要读取文件内容，只拿元信息。

示例

```
p = Path("test.py")
stat_res = p.stat()
print(stat_res.st_size) # 文件字节大小
```

## 9. `path.suffix`

代码：`if path.suffix != ".py": return ()`

### 作用

获取文件后缀（扩展名）。**纯路径字符串处理，不访问磁盘**。
规则：取最后一个 `.` 后面部分。

示例

```
Path("app.py").suffix      # .py
Path("data.tar.gz").suffix # .gz 只取最后一段
Path("README").suffix      # "" 空字符串，无后缀
```

> 
> 如果需要全部后缀用 `.suffixes` 返回列表。

## 10. `path.read_text(encoding="utf-8", errors="replace")`

### 作用

读取整个文件内容，返回字符串。**读取磁盘文件内容**。
参数说明：

- `encoding="utf-8"`：按 utf8 解码
- `errors="replace"`：遇到无法解码的字节，替换成 �，**不会直接抛出 UnicodeDecodeError**

底层：打开文件（`open()`），一次性读全部内容，关闭文件。

> 
> ⚠️ 大文件会一次性加载全部到内存。

示例

```
p = Path("test.py")
text = p.read_text(encoding="utf-8", errors="replace")
```

---

# 整体梳理代码里 path 的执行流程

```
for path in sorted(root.rglob("*")):
    # path 是遍历出来的Path对象
    if not path.is_file():                 # 磁盘IO：是不是普通文件
        continue
    if any(part in SKIP_DIRS for part in path.parts): # 纯字符串拆分路径
        continue

    relative = str(path.relative_to(root)) # 计算相对路径，转字符串
    symbols = self._python_symbols(path)   # 传入path对象

    RepoFile(
        path=relative,
        symbols=symbols,
        size_bytes=path.stat().st_size,    # stat系统调用拿文件大小
    )
```

`_python_symbols` 内部继续使用 path：

1. `path.suffix` 判断后缀是不是.py
2. `path.read_text()` 读取源码文本
3. `ast.parse` 解析源码得到抽象语法树，提取 class/def 名称

---

# 补充：Path 对象的特性总结

1. **惰性**：构造 Path 对象不会访问磁盘；只有 `is_file / stat / read_text` 才触发 IO
2. **不可变**：Path 实例一旦创建，路径不能修改；操作后返回新 Path
3. 跨平台：Windows / Linux 自动适配路径分隔符，不需要手动拼接`/`
4. 区分：
   - 纯路径计算：`.parts` `.relative_to` `.suffix` → **无 IO，速度快**
   - 文件系统查询：`.is_file()` `.stat()` `.read_text()` → **触发磁盘 IO，慢，可能抛异常**