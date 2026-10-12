# Music TUI

一个终端音乐播放器，支持 QQ 音乐和网易云音乐。左侧歌单及歌曲总数，右侧歌曲，底部当前歌曲与播放进度。

```sh
music-tui
```

首次使用默认进入 QQ 音乐，之后沿用上次的平台；`--source` 可明确指定平台。
在界面内按 `F2`，选择 QQ 音乐、网易云 Enhanced（默认接入）或网易云官方，无需退出。
首次使用按 `l`，用当前平台的手机 App 扫码并确认。二维码直接显示在终端内。
登录页适配常见的 80 列 × 24 行终端。登录成功后读取当前账号的歌单。QQ 音乐包含「我喜欢」、自建及收藏歌单。
登录凭证保存在本机，后续启动会检查登录状态。网易云官方令牌过期后需重新扫码。

| 按键 | 操作 |
| --- | --- |
| `F1` | 打开快捷键帮助 |
| `F2` | 打开平台菜单，切换 QQ / 网易云及其接入方式 |
| `F3` | 查看待播队列；队列内 `Delete` 删除手动待播歌曲 |
| `F4` | 查看当前歌曲的同步歌词；未播放时查看选中歌曲 |
| `F5` | 重试原来的失败歌曲；mpv 退出时重建播放器 |
| `F6` | 继续当前账号上次保存的播放会话 |
| `F7` | 打开 / 关闭独立播放页 |
| `F8` | 查看最近播放；`Tab` 切换最近歌曲 / 来源，`o` 打开来源 |
| `a` | 将选中歌曲加入手动待播，优先于原列表后续歌曲播放 |
| `s` | 喜欢 / 取消喜欢选中歌曲（QQ、网易云 Enhanced） |
| `w` | 将选中歌曲加入已有可写歌单（QQ、网易云 Enhanced） |
| `g` | 返回当前播放来源，并定位当前歌曲 |
| `Ctrl+R` | 刷新当前列表；歌单内筛选会重新读取完整歌单 |
| `l` | 扫码登录 |
| `/` | 聚焦全站搜索框，输入后回车搜索 |
| `f` | 聚焦歌单内搜索框，输入歌名或歌手后回车筛选 |
| `Tab` / `Shift+Tab` | 切换搜索、歌单、歌曲区域 |
| `↑` / `↓` | 选择歌单或歌曲 |
| `Enter` | 打开歌单、播放选中歌曲 |
| `Space` | 暂停 / 继续 |
| `n` / `p` | 下一首 / 上一首 |
| `←` / `→` | 后退 / 前进 5 秒 |
| `-` / `+`（或 `=`） | 调整音量 |
| `[` / `]` | 上一页 / 下一页 |
| `PageUp` / `PageDown` | 歌曲区域中上一页 / 下一页 |
| `r` | 切换顺序播放 / 列表循环 / 单曲循环 / 随机播放 |
| `u` | 选择播放音质 |
| `Esc` | 从搜索返回歌曲；关闭登录页 |
| `q` / `Ctrl+C` | 退出并停止播放 |

登录页用 `r` 刷新过期的二维码。搜索框中可以正常输入空格和上述字母快捷键。
歌曲区域显示接口返回的歌曲总数、当前页 / 总页数和本页歌曲数。翻页会替换本页内容。
打开歌单后按 `f` 搜索歌单内的歌名或歌手，支持部分匹配和英文大小写忽略。
首次筛选会读取整个歌单，包含未浏览的分页；后续筛选复用已读取的歌曲信息。
清空歌单内搜索框并回车，可返回完整歌单。重新打开歌单会刷新歌曲信息。
筛选后显示歌单总数和匹配数量，匹配结果支持分页。按回车播放会以匹配结果建立队列。
浏览或筛选本身不改变当前播放队列。全站搜索仍使用顶部搜索框。
选择一首歌会以整个歌单或搜索结果建立播放队列，从选中歌曲开始播放，切歌可跨页。
默认顺序播放，到列表末尾停止。按 `r` 切换列表循环、单曲循环或随机播放，顶部显示当前模式。
列表循环会在整个列表的首尾衔接。单曲循环在歌曲结束后重播，手动 `n` / `p` 仍会切歌。
随机播放首次切歌时加载整个列表的歌曲信息，再打乱顺序，每轮不重复播放。
`p` 返回随机播放历史中的上一首，`n` 可沿历史继续。单首列表会重复播放。
浏览其他歌单或搜索结果不会替换正在播放的队列；再次按回车播放才会替换。

听歌时可浏览其他歌单或搜索结果，选中歌曲后按 `a` 加入手动待播。
手动待播按添加顺序播放，听完后继续原列表；不改变原列表的分页位置或随机播放历史。
`n` 优先播放手动待播；手动歌曲播放时按 `p` 可回到原列表中被插入歌曲打断的位置。
单曲循环也适用于正在播放的手动歌曲，手动按 `n` 仍会继续切歌。
`F3` 显示手动待播和原列表已加载的后续歌曲，更多页面会在播放时继续读取；
随机模式下会先读取完整列表，队列页按实际随机待播顺序显示。
队列页会随切歌实时更新。选中手动待播项后，按 `Ctrl+↑` / `Ctrl+↓` 调整顺序，
按 `Delete` 删除；`Ctrl+Delete` 清空手动待播，当前歌曲和原列表继续播放。
队列页内可用 `Space` 暂停 / 继续，用 `n` / `p` 切歌。
按 Enter 开始新的播放列表会清空手动待播。平台切换同样清空当前队列，平台之间不混播。

音频加载失败后按 `F5`，会重新获取原队列项的播放地址，不受正在浏览的列表影响。
播放器进程退出时，`F5` 会重建 mpv，并恢复最近记录的进度、暂停状态和音量。
权限和音质错误仍需手动处理，不自动跳过歌曲或降低音质。

音量、所选音质和播放模式按平台保存。播放队列、手动待播、筛选结果、随机历史和进度
按平台与登录账号分别保存；播放中每五秒保存一次，退出或切平台时也保存。
重新启动不会自动播放，按 `F6` 才继续当前账号的上次会话，并重新取得播放地址。
切回之前的平台也可按 `F6` 恢复。会话文件不含 Cookie、凭证或播放地址。
状态文件位于 `~/.local/share/music-tui/preferences.json` 与 `sessions.json`，
支持 `XDG_DATA_HOME`，权限为 `0600`。

按 `F7` 打开独立播放页，集中查看当前歌曲、播放状态、进度、音量、音质、模式、来源和下一首。
底部按钮与列表页共用播放控制；`Space` 暂停 / 继续，`n` / `p` 切歌，左右箭头快进退。
播放页内可打开队列、歌词、音质和帮助，关闭弹窗后回到播放页。
按 `F7` 或 `Esc` 返回浏览页，不会停止播放；按 `/` 搜索、`f` 筛选或 `g` 定位当前歌曲时也会返回浏览页。

按 `F8` 查看当前账号的最近播放，按 Enter 以单曲队列重播选中歌曲，重新获取播放地址。
按 `o` 打开原歌单或搜索来源，保留当前播放；按 `Tab` 切换到去重后的最近来源列表。
同一首歌重播后移到最前，最多保留 100 首。匿名播放不写入历史，平台和账号之间分别保存。
最近播放文件为 `~/.local/share/music-tui/history.json`，支持 `XDG_DATA_HOME`，权限为 `0600`；
只保存曲目信息、来源和播放时间，不保存 Cookie 或播放地址。

`F4` 显示逐行同步歌词及可用译文，左右箭头可快进退；没有时间信息时可手动滚动阅读。
歌曲变化后关闭旧歌词页。`s` 与 `w` 针对歌曲列表中选中的歌曲，成功后刷新歌单侧栏；
按 `Ctrl+R` 更新正在浏览的歌曲内容。网易云官方接入暂不支持歌词和歌单写操作，
会明确提示不支持，不切换后端。

播放中按 `u` 查询当前歌曲的可用音质。未播放时，查询歌曲列表中选中的歌曲。
QQ 音乐菜单一次请求各档授权地址，再检查音频服务器是否可以访问，只显示有有效地址的档位，
并按从高到低排列。没有权限或服务器返回 404 的档位不会出现在菜单里。
QQ 音乐候选档位为臻品母带、臻品全景声、臻品音质、Hi-Res 高解析、SQ 无损、HQ 高品质和标准音质。
高级档位使用对应的独立音源。
本次运行期间，后续歌曲会使用所选音质。播放中切换会保留进度和暂停状态。
顶部显示所选音质，底部显示当前播放音质、进度条和音量。
歌曲音源、账号及会员权限决定所选音质是否可用。不可用时显示错误，不自动降低音质；
若无法取得所选音质的播放地址，当前歌曲继续以原音质播放。

界面预览（示例歌曲）：[80×24 终端](docs/tui-preview.png) · [平台切换菜单](docs/platform-switch.svg)。
新增功能预览：[待播队列](docs/queue-preview.svg) · [同步歌词](docs/lyrics-preview.svg)。
本轮预览：[独立播放页](docs/playback-preview.svg) · [最近播放](docs/history-preview.svg)。

## 从源码运行

需要 Python 3.13+、[uv](https://docs.astral.sh/uv/) 和 [mpv](https://mpv.io/)。

```sh
brew install uv mpv
git clone https://github.com/FlyTOmeLight/music-tui.git
cd music-tui
uv sync --locked
uv run music-tui
```

网易云启动方式见下一节。

项目复用 [Textual](https://github.com/Textualize/textual) 处理终端输入与布局，
[QQMusicApi](https://github.com/L-1124/QQMusicApi/tree/v0.7.3) 处理扫码、会话和音乐接口，
[terminal-qrcode](https://pypi.org/project/terminal-qrcode/) 渲染紧凑二维码。
官方网易云请求用 [cryptography](https://cryptography.io/) 执行 RSA 签名，
用 [qrcode](https://pypi.org/project/qrcode/) 与 Pillow 生成登录二维码 PNG。
音频由独立的 mpv 进程播放，通过本机 Unix socket 控制，不依赖 Python 音频绑定。
依赖的具体版本由 `uv.lock` 固定。

QQ 音乐账号数据继续位于 `~/.local/share/qqmusic-tui/`，也支持 `XDG_DATA_HOME`。
`credential.json` 的权限为 `0600`。删除它即可清除本机保存的登录状态。

## 网易云音乐

网易云默认使用 [NeteaseCloudMusicApiEnhanced](https://github.com/NeteaseCloudMusicApiEnhanced/api-enhanced)。
不需要开发者应用 ID、私钥或设备授权参数。首次运行交互式向导：

```sh
uv run music-tui --setup-netease
```

选择「本机服务」即可。向导检查 Node.js 22+ 和 npx，自动选择空闲本机端口并保存服务地址。
已有服务时，选择「已有服务」并填写地址即可。详细步骤见[网易云配置指南](docs/netease-setup.md)。

macOS 本机服务由 launchd 在后台守护，执行启动命令后即可继续使用当前终端：

```sh
uv run music-tui --netease-service
```

服务异常退出后会自动重启，关闭终端不会停止服务；重复启动不会创建多个服务。
服务仅在当前登录会话运行，注销或重启电脑后需重新执行启动命令。
查看状态或主动停止服务：

```sh
uv run music-tui --netease-service-status
uv run music-tui --netease-service-stop
```

随后启动播放器：

```sh
uv run music-tui
```

按 `F2` 选择网易云 Enhanced，再按 `l`，用网易云音乐手机 App 扫码确认。
也可执行 `uv run music-tui --source netease`，直接进入网易云。
首次启动服务时，npx 会下载固定版本 `@neteasecloudmusicapienhanced/api@4.41.1`。
服务绑定 `127.0.0.1`，自动设置 `ENABLE_GENERAL_UNBLOCK=false` 和 `ENABLE_PROXY=false`。
服务停止后，播放器的网易云请求会提示连接错误。

服务配置保存在 `~/.config/music-tui/enhanced.json`，支持 `XDG_CONFIG_HOME`。
服务日志保存在 `~/.config/music-tui/enhanced-service.log`；设置 `XDG_CONFIG_HOME`
时使用 `$XDG_CONFIG_HOME/music-tui/enhanced-service.log`。
后续无需重新运行向导。`--netease-api-url` 可临时覆盖保存的服务地址。
已有服务也必须关闭全局解锁功能，具体命令见配置指南。
其他操作系统暂不支持上述守护命令，可按配置指南使用 npx 手动运行服务。

官方开放平台保留为可选接入。已有获批应用凭证和设备参数时，执行：

```sh
uv run music-tui --netease-backend official --setup-netease
uv run music-tui --source netease --netease-backend official
```

官方配置方法见[指南中的可选官方接入](docs/netease-setup.md#可选官方接入)。
已有官方配置不会被 Enhanced 向导覆盖。两种方式的请求失败均显示错误，不会自动切换后端。

两个后端均支持歌单内筛选和现有播放模式，拒绝试听地址和音质降级结果。
切换音质会保留进度和暂停状态。Enhanced 凭证保存在
`~/.local/share/music-tui/netease/netease-credential.json`；官方凭证单独保存在
`~/.local/share/music-tui/netease-official/`。文件权限为 `0600`，支持 `XDG_DATA_HOME`。
平台切换会停止当前播放并清空队列，再恢复目标平台的登录状态和歌单。
修改本机配置后需重新启动 Music TUI。
实现选择及接口来源见[网易云接入说明](docs/netease-integration.md)。

## 验证

```sh
uv run black src tests
uv run black --check src tests
uv run ruff check src tests
uv run pytest -q
```

测试覆盖键盘操作、搜索取消、队列独立性、跨页切歌、列表分页、总数显示、
跨页歌单内搜索、筛选结果播放与取消、循环方式、权限错误、二维码显示与登录状态转换、
凭证文件权限，以及真实 mpv 的播放、暂停、定位、音量、结束事件和退出清理。
新增测试覆盖手动待播与删除、失败歌曲重试、mpv 断线重建、会话与账号隔离、
随机历史恢复、歌词同步，以及喜欢和加歌单的离线接口契约。
mpv 集成测试使用本地产生的静音 WAV，不访问网络。
GitHub Actions 在 `main`、`release-v*`、`bugfix-*` 的提交和 Pull Request 上运行
Linux/macOS 检查，包括真实 mpv 测试、构建以及仓库外全新环境的 wheel 安装验证。
本地构建后也可执行 `bash scripts/check_install.sh` 验证安装。

开发时另已实测 QQ 搜索、真实二维码获取、账号登录恢复、个人歌单总数与跨页读取，
以及 QQ 音频地址交给 mpv 解码播放。

许可证：GPL-3.0-or-later，见 `LICENSE`。
版本更新内容见 [更新记录](CHANGELOG.md)。

## 开发约定

功能研发和发版使用版本分支，例如 `release-v0.2.0`；每个缺陷使用单独的修复分支，
例如 `bugfix-playback-retry`。完整分支约定见 [AGENTS.md](AGENTS.md#commit--pull-request-guidelines)。

每次修改 Python 代码后执行 `uv run black src tests`，提交前检查 Black、Ruff 和 pytest。
所有 Python 文件、函数及方法都需要简短的 docstring，说明职责和重要行为。
独立任务可交给子代理并行处理，由主代理整合并验证。
代码组织调研与建议见 [Python 项目结构](docs/python-project-structure.md)。
