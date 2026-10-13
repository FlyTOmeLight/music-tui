# Python 项目结构调研

调研日期：2026-10-12。查看第一方源码及官方文档。以下结论针对本仓库的 Textual 音乐播放器；网易云接入后已按职责拆分平台模块。

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

## 当前项目的组织方式

网易云接入已采用共享模型和平台模块：

```text
src/music_tui/
├── __init__.py
├── app.py           # 应用入口、界面协调、账号切换与播放命令
├── playback_queue.py   # 队列导航、手动待播优先级与编辑、只读视图及加载事件管理
├── playback_session.py # 播放快照类型、编解码与恢复前校验
├── models.py        # 歌曲、歌单、分页、音质和登录状态
├── player.py        # mpv 生命周期与通信
└── sources/
    ├── __init__.py  # UI 实际使用的 MusicSource 接口
    ├── qqmusic.py   # QQ SDK 数据转换、凭证和扫码适配
    └── netease.py   # 网易云 HTTP API、Cookie 和扫码适配
tests/
docs/
pyproject.toml
```

两个平台将各自的外部响应转换成项目模型。登录会话只给界面返回二维码图片和状态；
账号凭据留在平台模块中。UI 和 mpv 无需了解 SDK 凭证类型或网易云 Cookie。

每次启动选择一个平台，复用同一套队列和筛选逻辑。平台接口仅包含现有 UI 使用的方法，
不引入插件发现或注册表。两平台的账号目录独立。

队列导航由 `PlaybackQueue` 集中持有，界面浏览和播放队列各自独立。
原列表与手动待播的优先级、返回位置、身份编辑和失败导航重试也由队列管理；
界面使用只读视图和导航结果，不直接修改队列字段或随机历史。队列规则通过该
interface 测试，界面测试保留快捷键、播放事件、存储与异步协作。
`PlaybackSnapshot` 保留现有 JSON 格式，在恢复之前完成数据校验；`app.py` 协调
账号、播放器和界面，不再自行维护分页导航与快照解码规则。

## 测试与日常维护

保留独立的 `tests/`，按可观察行为命名测试。平台测试使用离线响应；播放器测试继续验证真实 mpv 行为。pytest 官方文档支持源码和测试分开的布局，推荐新项目使用 `importlib` 导入模式；本项目当前没有导入冲突，可待测试目录扩展时评估。[pytest 测试布局说明](https://docs.pytest.org/en/stable/explanation/goodpractices.html#choosing-a-test-layout)

每个 Python 文件写模块 docstring，每个函数及方法写用途 docstring；复杂分支解释原因和约束。完成代码修改后运行 `uv run black src tests`，再运行 `uv run ruff check src tests` 和受影响的 pytest 测试。格式化和静态检查配置统一放在 `pyproject.toml`。
