# JSON 批量字段筛选与修改工具

Python 3.12+ / PySide6 中文桌面工具，支持递归扫描、正则筛选、跨文件编辑暂存及安全写回。不使用数据库，不依赖 VS Code。

## 启动

安装 [uv](https://docs.astral.sh/uv/)，在项目目录运行：

```sh
uv sync
uv run json-update
```

也可使用 Python 虚拟环境执行 `pip install -e .`，然后运行 `json-update` 或 `python -m json_update`。

## 使用

1. 选择主目录，后台递归扫描 `.json` 文件。不跟随目录或文件符号链接。
2. 文件正则匹配相对路径（统一使用 `/`），例如 `.*config.*\.json$`；留空匹配全部。
3. 字段正则匹配名称或完整路径。默认 `^label$`；留空显示全部字符串字段。多行输入作为一条 Python 正则处理，换行不是多条规则的分隔符。
4. 点击左侧文件，逐条修改右侧字段。文件前的 `*` 表示未保存；切换文件或筛选条件保留修改。
5. 点击「完成并保存」写入所有已修改文件，包括当前筛选隐藏的文件。切换根目录或退出会提示保存、放弃或取消。

普通字段路径示例：`$.data.name`、`$.items[0].label`、`$["包含.点的键"]`。

对 `annotation.result` 字符串专项解析一层 JSON。例如字段路径：

```text
$.annotation.result::<json>.videoSegmentTool.result[0].label
```

输入 `^label$` 可逐条修改视频片段标签。保存后 `annotation.result` 仍是字符串；其他 JSON 字符串不自动解析。内部 JSON 无效时，该字段不展示，其他普通字段仍可编辑。只编辑字符串，不提供同值批量替换。

## 保存行为与限制

- 只支持 UTF-8（可带 BOM）。拒绝重复键、NaN、Infinity 及超出安全浮点精度或范围的数字，避免静默丢失数据。
- 尽量保留缩进、换行、BOM、末尾换行、键顺序及 Unicode 转义风格。内部 JSON 尽量保留紧凑或缩进形式；空白、数字文本及转义形式不保证逐字节一致。数字使用 Python `json` 的整数和双精度浮点表示；无法保持原数值的高精度小数会拒绝加载。
- 原始文件与读取时内容不一致则拒绝覆盖。不要在其他程序中同时修改正在编辑的文件；检查与替换之间无法提供跨进程文件锁。
- 保存先写同目录临时文件，刷新到磁盘，再替换原文件。每个文件独立保存，不是整个批次的事务。失败文件保留修改，可修复权限等问题后重试。
- 外部冲突需手动保留输入内容，再重新选择目录（先切换到其他目录）以重新加载；V1 不自动合并冲突、不保存历史备份。错误详情在窗口底部「查看错误」中查看。
- 扫描与解析在后台执行。Python 正则不提供执行超时，请避免灾难性回溯表达式。

## 测试

```sh
uv sync --group dev
uv run pytest
```

无显示器环境运行界面测试：

```sh
QT_QPA_PLATFORM=offscreen uv run pytest
```

Linux 上加载 Qt 需要系统图形运行库（例如 Ubuntu 的 `libgl1`、`libegl1`、`libopengl0`），即使使用 offscreen 模式也需要这些动态库。只验证不依赖 Qt 的核心逻辑可运行 `uv run pytest tests/test_session.py`。

## Windows 打包

日常可以继续在 Linux 开发。仓库包含 GitHub Actions 工作流 `.github/workflows/build-windows.yml`：推送到 `main` / `master`、推送 `v*` 标签或手动运行 **Build Windows EXE** 时，在 Windows 构建机上安装锁定依赖、运行测试并打包。

构建成功后，进入 GitHub 仓库的 **Actions → Build Windows EXE → 对应运行 → Artifacts**，下载 `JSON-Batch-Editor-Windows-x64`，解压即可得到 EXE，复制到 Windows 直接验证，无需安装 Python。构建产物保留 30 天。首次使用需先将本地仓库推送到 GitHub 并启用 Actions；当前本地配置不会自行上传或发布文件。

也可在 Windows 本地打包：

在 Windows 上使用 Python 3.12+ 执行：

```powershell
uv sync --group build
uv run --group build pyinstaller --clean --noconfirm json-update.spec
```

输出单文件 GUI 程序：`dist/JSON-Batch-Editor.exe`。运行不需要安装 Python。PyInstaller 不支持从 Linux 交叉打包 Windows EXE。

发布前须在目标 Windows 机器上验证 EXE 启动、中文目录、目录选择、多文件编辑、关闭提示、只读文件及安全保存。本仓库提供构建配置，Windows 运行验收需单独完成。
