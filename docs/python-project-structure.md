# Python 项目结构调研

调研日期：2026-10-12。查看第一方源码及官方文档。以下结构建议针对本仓库的 Textual 音乐播放器，不代表已完成重构。

## 结论

保留 `src/music_tui/`、`tests/`、`docs/` 和根目录 `pyproject.toml`。`src` 布局把可导入的代码与仓库工具隔开，减少从当前目录误导入源码而掩盖打包错误的问题。[PyPA 布局说明](https://packaging.python.org/en/latest/discussions/src-layout-vs-flat-layout/)

本次观察到的共同做法是按职责组织模块，而非统一采用某一种目录模板。下面的应用建议是根据这些源码和本仓库规模作出的判断。

## 三个参考项目

| 项目 | 源码中的组织方式 | 对本仓库的启发 |
| --- | --- | --- |
| Textual | 使用 `src/textual/`，将 `widgets/`、`css/`、`drivers/` 等职责分成子包，同时保留 `app.py` 等模块。 | 弹窗和组件有独立行为时再抽取；不需要给每个类建立文件。[源码目录](https://github.com/Textualize/textual/tree/main/src/textual) |
| HTTPX | 使用根目录 `httpx/`；内部模块包括 `_client`、`_models` 和 `_auth`，`__init__.py` 集中声明公开导出。传输接口明确区分请求处理与关闭资源。 | 音乐平台边界应负责 SDK 数据转换和资源生命周期；UI 使用项目自己的歌曲类型。[公开入口](https://github.com/encode/httpx/blob/master/httpx/__init__.py)、[传输接口](https://github.com/encode/httpx/blob/master/httpx/_transports/base.py)、[打包配置](https://github.com/encode/httpx/blob/master/pyproject.toml) |
| pytest | 使用 `src/pytest/` 提供公开 API，具体实现来自 `src/_pytest/` 的职责模块。 | 学习清楚的职责和导入方向；当前应用不需要复制其公开包与私有包的双层结构。[公开入口源码](https://github.com/pytest-dev/pytest/blob/main/src/pytest/__init__.py) |

Textual 的开发配置同时声明 Black、pytest 和 pytest-asyncio，并把测试放在 `tests/`。这与本项目已有工具接近，无需为目录整理更换工具链。[Textual 配置](https://github.com/Textualize/textual/blob/main/pyproject.toml)

## 当前项目的执行顺序

1. 继续使用现有 `app.py`、`music.py`、`player.py` 三个职责模块。`player.py` 只处理 mpv 进程和通信，不引用音乐平台 SDK。
2. 下一次修改相关界面时，优先从较大的 `app.py` 抽出登录和音质弹窗到 `screens.py`。播放队列及其状态转换仍集中在应用中，避免同一状态被多个模块维护。
3. 实际接入网易云时，再把歌曲、歌单、分页结果移到 `models.py`，把 QQ 音乐访问移到 `sources/qqmusic.py`，新增 `sources/netease.py`。平台实现负责把各自 SDK 数据转换成项目模型。
4. 根据两个实现实际共有的调用定义小型接口，只覆盖搜索、歌单、播放地址等 UI 使用的能力。登录可能因平台不同而分开处理。不预建插件发现、注册表或多层服务包装。

预计接入第二个平台后的目录如下；现在不用提前创建空文件：

```text
src/music_tui/
├── __init__.py
├── app.py           # 应用入口、队列、界面协调
├── screens.py       # 登录和音质弹窗
├── models.py        # 项目自己的歌曲和歌单数据
├── player.py        # mpv 生命周期与通信
└── sources/
    ├── __init__.py
    ├── qqmusic.py    # QQ 音乐访问与数据转换
    └── netease.py    # 网易云访问与数据转换
tests/
docs/
pyproject.toml
```

## 测试与日常维护

保留独立的 `tests/`，按可观察行为命名测试。平台测试使用离线响应；播放器测试继续验证真实 mpv 行为。pytest 官方文档支持源码和测试分开的布局，推荐新项目使用 `importlib` 导入模式；本项目当前没有导入冲突，可待测试目录扩展时评估。[pytest 测试布局说明](https://docs.pytest.org/en/stable/explanation/goodpractices.html#choosing-a-test-layout)

每个 Python 文件写模块 docstring，每个函数及方法写用途 docstring；复杂分支解释原因和约束。完成代码修改后运行 `uv run black src tests`，再运行 `uv run ruff check src tests` 和受影响的 pytest 测试。格式化和静态检查配置统一放在 `pyproject.toml`。
