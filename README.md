# Music TUI

一个终端音乐播放器，支持 QQ 音乐和网易云音乐。左侧歌单及歌曲总数，右侧歌曲，底部当前歌曲与播放进度。

```sh
music-tui
```

默认使用 QQ 音乐。在界面内按 `F2`，选择 QQ 音乐、网易云官方或网易云 Enhanced，无需退出。
首次使用按 `l`，用当前平台的手机 App 扫码并确认。二维码直接显示在终端内。
登录页适配常见的 80 列 × 24 行终端。登录成功后读取当前账号的歌单。QQ 音乐包含「我喜欢」、自建及收藏歌单。
登录凭证保存在本机，后续启动会检查登录状态。网易云官方令牌过期后需重新扫码。

| 按键 | 操作 |
| --- | --- |
| `F2` | 打开平台菜单，切换 QQ / 网易云及其接入方式 |
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

按 `F2` 可在应用内切换平台。网易云优先使用官方开放平台；Enhanced 是需自行启动本地服务的第二选择。

**官方接入需要先申请开发者身份。只有网易云听歌账号还不够。**
个人入驻通过后，在开放平台的应用管理中初始化应用，取得 `appId` 和 `privateKey`。
当前原生接入还要求设备参数，其中部分值需向网易云确认，不能随意填写。
从申请入口、保存私钥、填写设备文件到启动验证，请按[网易云配置指南](docs/netease-setup.md)逐步操作。

取得应用凭证和网易云确认的设备字段后，运行交互式配置：

```sh
uv run music-tui --setup-netease
uv run music-tui
```

向导只要求填写应用 ID、私钥，以及网易云确认的 `channel`、`deviceType`、`os`、`brand`。
私钥输入隐藏，支持单行 Base64 或已有 PEM 文件路径。
设备 ID、客户端版本自动生成；系统版本、型号、IP 和网络类型自动检测，检测失败时才会询问。
配置保存到 `~/.config/music-tui/`，支持 `XDG_CONFIG_HOME`，后续启动自动读取，无需设置 `export`。
重新运行向导时，回车可复用已有设置和私钥。

进入界面后按 `F2` 选择网易云官方，再按 `l` 扫码。也可用 `--source netease` 直接启动官方网易云。
向导完成本机配置，不代替开发者申请或扫码登录。官方请求直接访问网易云，无需运行本地 API 服务。
令牌过期后需重新扫码。

没有官方应用凭证时，可显式选择
[NeteaseCloudMusicApiEnhanced](https://github.com/NeteaseCloudMusicApiEnhanced/api-enhanced)。
需要 Node.js 22+，先在一个终端运行：

```sh
HOST=127.0.0.1 PORT=3000 ENABLE_GENERAL_UNBLOCK=false ENABLE_PROXY=false \
  npx --yes @neteasecloudmusicapienhanced/api@4.41.1
```

保持该终端运行。在 Music TUI 内按 `F2` 选择网易云 Enhanced 即可。也可直接启动到该方式：

```sh
uv run music-tui --source netease --netease-backend enhanced
```

Enhanced 服务地址默认为 `http://127.0.0.1:3000`，可用 `--netease-api-url` 修改。
服务必须设置 `ENABLE_GENERAL_UNBLOCK=false`；客户端不请求解锁接口。
官方配置、网络或权限失败时不会自动切换到 Enhanced。

两个后端均支持歌单内筛选和现有播放模式，拒绝试听地址和音质降级结果。
切换音质会保留进度和暂停状态。官方凭证保存在 `~/.local/share/music-tui/netease-official/`；
Enhanced 凭证仍保存在 `~/.local/share/music-tui/netease/netease-credential.json`。
凭证文件权限为 `0600`，支持 `XDG_DATA_HOME`，两后端与 QQ 凭证互不覆盖。
平台切换会停止当前播放并清空当前队列，随后恢复目标平台的登录状态和歌单。
本机配置和环境变量在启动时读取；修改配置后需重新启动 Music TUI。
`NETEASE_APP_ID`、`NETEASE_PRIVATE_KEY_FILE`、`NETEASE_DEVICE_FILE` 可逐项覆盖向导配置。

官方接入已按公开文档实现并通过离线测试；真实登录和会员播放仍需获批应用凭证验证。
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
mpv 集成测试使用本地产生的静音 WAV，不访问网络。

开发时另已实测 QQ 搜索、真实二维码获取、账号登录恢复、个人歌单总数与跨页读取，
以及 QQ 音频地址交给 mpv 解码播放。

许可证：GPL-3.0-or-later，见 `LICENSE`。

## 开发约定

每次修改 Python 代码后执行 `uv run black src tests`，提交前检查 Black、Ruff 和 pytest。
所有 Python 文件、函数及方法都需要简短的 docstring，说明职责和重要行为。
独立任务可交给子代理并行处理，由主代理整合并验证。
代码组织调研与建议见 [Python 项目结构](docs/python-project-structure.md)。
