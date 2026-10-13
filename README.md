# Music TUI

在终端里听 QQ 音乐和网易云音乐。扫码登录自己的账号，浏览歌单、搜索歌曲和歌手，用键盘或鼠标控制播放。

Music TUI 支持跨页播放、手动待播、同步歌词、音质选择和播放会话恢复。浏览其他列表时，当前音乐会继续播放。

![Music TUI 夜间界面](https://raw.githubusercontent.com/FlyTOmeLight/music-tui/main/docs/tui-preview.png)

[日间界面](https://raw.githubusercontent.com/FlyTOmeLight/music-tui/main/docs/tui-day-preview.png) · [歌手详情](https://raw.githubusercontent.com/FlyTOmeLight/music-tui/main/docs/artist-preview.png) · [更新记录](https://github.com/FlyTOmeLight/music-tui/blob/main/CHANGELOG.md)

## 从 PyPI 安装

PyPI 包名为 [`melody-tui`](https://pypi.org/project/melody-tui/)，安装后的启动命令为 `music-tui`。

需要 **Python 3.13+**、[uv](https://docs.astral.sh/uv/) 和 [mpv](https://mpv.io/)。音频由本机 mpv 进程播放。

macOS 先安装 uv 和 mpv：

```sh
brew install uv mpv
```

Linux 请先用系统包管理器安装 mpv，并安装 uv。然后从 PyPI 安装并启动播放器：

```sh
uv tool install --python 3.13 melody-tui
music-tui
```

播放器通过 Unix socket 控制 mpv；当前 CI 验证 Linux 和 macOS。

升级和查看版本：

```sh
uv tool upgrade melody-tui
music-tui --version
```

`music-tui -v` 同样可以查看版本。查看所有命令行选项：`music-tui --help`。

## 第一次听歌

1. 运行 `music-tui`。首次进入 QQ 音乐，之后沿用上次使用的平台。
2. 按 `l`，用 QQ 音乐手机 App 扫描终端中的二维码并确认。二维码过期时按 `r` 刷新。
3. 在左侧选择歌单并按 `Enter`，再在右侧选歌并按 `Enter` 播放。
4. 按 `Space` 暂停或继续，按 `n` / `p` 切歌，按 `q` 退出。

QQ 歌单包含「我喜欢」、自建和收藏歌单。有效登录状态会保存在本机，后续启动会自动检查。
常见的 80×24 终端可以显示主界面和登录页。

按 `F2` 可在 QQ 音乐与网易云 Enhanced 之间切换。切换会停止当前播放并清空队列，各平台分别保存账号状态。
首次使用网易云请先完成下一节的配置。

## 网易云音乐配置

默认通过 [NeteaseCloudMusicApiEnhanced](https://github.com/NeteaseCloudMusicApiEnhanced/api-enhanced) 接入，不需要申请开发者应用。使用本机服务需要 **Node.js 22+ 和 npx**。

macOS：

```sh
brew install node
music-tui --setup-netease
music-tui --netease-service
music-tui --source netease
```

向导中选择「本机服务」，它会选择空闲端口并保存地址。第二条 Music TUI 命令才会启动服务。
进入播放器后按 `l`，用网易云音乐手机 App 扫码确认。

本机服务由 launchd 在后台运行，关闭终端不影响服务，异常退出后会自动重启。
注销或重启电脑后，需要再次运行 `music-tui --netease-service`。

```sh
music-tui --netease-service-status  # 查看服务状态
music-tui --netease-service-stop    # 停止服务
```

首次启动会下载固定版本 `@neteasecloudmusicapienhanced/api@4.41.1`。
服务绑定 `127.0.0.1`，自动设置 `ENABLE_GENERAL_UNBLOCK=false` 和 `ENABLE_PROXY=false`。

Linux 等其他系统可手动运行服务，保持该终端开启：

```sh
HOST=127.0.0.1 PORT=3000 ENABLE_GENERAL_UNBLOCK=false ENABLE_PROXY=false \
  npx --yes @neteasecloudmusicapienhanced/api@4.41.1
```

然后在另一个终端运行 `music-tui --setup-netease`，选择「已有服务」，填写 `http://127.0.0.1:3000`，再运行 `music-tui --source netease`。
已有服务必须关闭全局解锁功能。临时覆盖保存的地址可用：

```sh
music-tui --source netease --netease-api-url http://127.0.0.1:3000
```

已有获批应用凭证和设备参数的用户，也可通过命令行使用网易云官方接入：

```sh
music-tui --netease-backend official --setup-netease
music-tui --source netease --netease-backend official
```

官方接入不在 `F2` 菜单中，暂不支持歌词、喜欢状态和新建歌单；支持向自己的歌单添加歌曲。
详细配置、日志位置和官方接入参数见[网易云配置指南](https://github.com/FlyTOmeLight/music-tui/blob/main/docs/netease-setup.md)。

## 日常操作

### 找歌与管理歌单

- 按 `/` 输入歌名或歌手并回车，进行全站搜索。
- 打开歌单后按 `f`，按歌名或歌手筛选整个歌单，包含尚未浏览的分页。清空输入并回车恢复完整歌单。
- 按 `[` / `]` 翻页，也可点击列表下方的翻页按钮。先按 `Esc` 将焦点移出输入框。
- 点击歌手名或按 `i` 进入歌手详情，查看简介和歌曲；热门／最新排序覆盖整个歌手歌曲列表。按 `Esc` 返回原页面。
- 按 `s` 喜欢或取消喜欢选中歌曲，按 `w` 加入已有可写歌单，按 `Ctrl+N` 新建歌单。加入歌曲时也可选择新建歌单。

搜索结果的 `♥` / `♡` / `?` 分别表示已喜欢、未喜欢和状态未知。点击标记也能切换喜欢状态。
按 `Ctrl+R` 刷新当前列表和喜欢状态；查询失败时显示未知，修改失败时保留上次确认的状态。

点击歌曲、歌手或时长表头可排序，点击「序」恢复原顺序；`o` 也可切换本页排序。
歌曲排序只影响当前页显示，播放仍按来源顺序。歌单侧栏可按名称或累计播放量排序。

### 播放队列与手动待播

从歌单、搜索结果、筛选结果或歌手歌曲列表中选歌播放，会以该列表建立队列，并从所选歌曲开始跨页播放。
浏览其他列表或筛选歌曲不会改变队列；再次选歌播放才会替换队列。

按 `r` 切换顺序播放、列表循环、单曲循环和随机播放。顺序播放到末尾停止；随机播放按轮打乱，每轮不重复，`p` 可返回播放历史中的上一首。

浏览时选中歌曲并按 `a` 加入手动待播。它们按添加顺序优先播放，听完继续原列表。
按 `F3` 查看队列，在队列页可以：

| 按键 | 操作 |
| --- | --- |
| `Ctrl+↑` / `Ctrl+↓` | 调整选中手动待播歌曲的顺序 |
| `Delete` | 删除选中手动待播歌曲 |
| `Ctrl+Delete` | 清空手动待播，保留当前歌曲和原列表 |

开始新列表或切换平台会清空手动待播。按 `g` 返回当前播放来源，并定位歌曲。

### 播放页、歌词与音质

按 `F7` 打开独立播放页，通过「概览 / 歌词 / 队列」页签查看封面、歌曲信息、同步歌词和待播内容。
按 `F4` 直接查看歌词，支持可用译文；没有时间信息的歌词可手动滚动。按 `F7` 或 `Esc` 返回浏览页，音乐继续播放。

按 `u` 查看当前歌曲可用的音质，未播放时查看选中歌曲。
播放中切换音质会保留进度和暂停状态；后续歌曲继续使用首选音质。
音质取决于歌曲音源、账号和会员权限。首选音质不可用时，逐档尝试至标准音质，并显示实际音质；下一首仍从首选档开始。
网络、登录或播放器错误不会触发降档。所有档位都失败时显示具体错误，等待重试或手动切歌。

### 继续上次播放与最近记录

音量、音质和播放模式按平台保存。队列、手动待播、筛选结果、随机历史和进度按平台与账号分别保存。
重新启动后按 `F6` 继续上次会话，应用会重新获取播放地址；启动时不会自动播放。

按 `F8` 查看当前账号的最近播放，按 `Enter` 以单曲队列重播。
`Tab` 切换最近歌曲／来源，`o` 打开原来源并保留当前播放。最多保留 100 首歌曲，匿名播放不写入历史。

## 快捷键速查

输入框内可以正常输入空格和字母；按 `Esc` 返回歌曲区域后再使用字母快捷键。`F1` 可随时打开帮助。

| 按键 | 操作 |
| --- | --- |
| `F1` / `F2` | 帮助 / 切换平台 |
| `F3` / `F4` | 队列 / 歌词 |
| `F5` / `F6` | 重试失败操作或恢复播放器 / 继续上次会话 |
| `F7` / `F8` | 独立播放页 / 最近播放 |
| `F9` / `Shift+F9` | 手动切换日间、夜间 / 恢复跟随系统 |
| `l` | 扫码登录 |
| `/` / `f` | 全站搜索 / 歌单内筛选 |
| `Tab` / `Shift+Tab` | 切换焦点区域 |
| `↑` / `↓` / `Enter` | 选择 / 打开歌单或播放歌曲 |
| `[` / `]` | 上一页 / 下一页 |
| `PageUp` / `PageDown` | 歌曲区域内翻页 |
| `Space` / `n` / `p` | 暂停或继续 / 下一首 / 上一首 |
| `←` / `→` | 后退 / 前进 5 秒 |
| `-` / `+`（或 `=`） | 调整音量 |
| `r` / `u` | 播放模式 / 音质 |
| `a` / `g` | 加入手动待播 / 定位当前歌曲 |
| `s` / `w` / `Ctrl+N` | 喜欢 / 加入歌单 / 新建歌单 |
| `i` / `o` / `Ctrl+R` | 歌手详情 / 本页排序 / 刷新列表 |
| `Esc` | 返回歌曲区域或关闭当前页面 |
| `q` / `Ctrl+C` | 退出并停止播放 |

底部的上一首、暂停／继续、下一首按钮也可直接点击。主题默认跟随系统，手动选择会保存。

## 常见问题

| 现象 | 处理方式 |
| --- | --- |
| mpv 不可用或意外退出 | 确认已安装 mpv 且在 `PATH` 中；按 `F5` 重建播放器，恢复已记录的进度、暂停状态和音量。 |
| 歌曲加载失败 | 按 `F5` 重试原失败操作；重试不受当前浏览列表影响。按 `n` 可手动切歌。 |
| 二维码过期或登录失效 | 登录页按 `r` 刷新二维码；按 `l` 重新扫码。 |
| 网易云服务无法连接 | macOS 用 `--netease-service-status` 检查状态；确认服务已启动、地址与端口一致。 |
| 无法选择某档音质 | 菜单只列出该歌曲当前可用的音质，检查账号权限和歌曲音源。 |
| 歌词加载失败 | 在歌词页签按 `F5` 重试。 |
| 快捷键被当作文字输入 | 按 `Esc` 离开输入框；英文方括号 `[` 和 `]` 分别用于翻页。 |

## 本机数据

以下为默认路径。数据文件支持 `XDG_DATA_HOME`，配置文件支持 `XDG_CONFIG_HOME`；登录凭证和播放状态文件权限为 `0600`。

| 内容 | 路径 |
| --- | --- |
| QQ 登录凭证 | `~/.local/share/qqmusic-tui/credential.json` |
| 网易云 Enhanced 登录凭证 | `~/.local/share/music-tui/netease/netease-credential.json` |
| 网易云官方账号数据 | `~/.local/share/music-tui/netease-official/` |
| 界面与播放偏好 | `~/.local/share/music-tui/preferences.json` |
| 播放会话 / 最近播放 | `~/.local/share/music-tui/sessions.json` / `history.json` |
| Enhanced 服务配置 / 日志 | `~/.config/music-tui/enhanced.json` / `enhanced-service.log` |

会话和历史只保存恢复播放所需的曲目信息、来源及状态，不保存 Cookie 或播放地址。
删除对应平台的登录凭证可清除本机保存的登录状态。修改服务配置后需重新启动播放器。

## 从源码运行与开发

```sh
git clone https://github.com/FlyTOmeLight/music-tui.git
cd music-tui
uv sync --locked
uv run music-tui
```

源码运行时，将上文的 `music-tui` 命令替换为 `uv run music-tui` 即可。
项目使用 Textual 构建界面、QQMusicApi 访问 QQ 音乐接口、mpv 播放音频，依赖版本由 `uv.lock` 固定。

修改 Python 代码后运行 Black，交付前执行：

```sh
uv run black src tests
uv run black --check src tests
uv run ruff check src tests
uv run pytest -q
uv build
bash scripts/check_install.sh
```

测试使用离线音乐响应；mpv 集成测试播放本地生成的静音 WAV。
GitHub Actions 在 Linux/macOS 上执行检查、构建及仓库外安装验证。
已登录 QQ 的开发者还可运行 `uv run python scripts/check_live_ui.py`，验证真实播放与不同终端尺寸的布局。

[项目结构](https://github.com/FlyTOmeLight/music-tui/blob/main/docs/python-project-structure.md) · [开发约定](https://github.com/FlyTOmeLight/music-tui/blob/main/AGENTS.md) · [网易云接入说明](https://github.com/FlyTOmeLight/music-tui/blob/main/docs/netease-integration.md) · [反馈问题](https://github.com/FlyTOmeLight/music-tui/issues)

许可证：[GPL-3.0-or-later](https://github.com/FlyTOmeLight/music-tui/blob/main/LICENSE)。
