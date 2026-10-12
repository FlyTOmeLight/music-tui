# 网易云音乐接入方案

操作步骤见[网易云配置指南](netease-setup.md)。应用内按 `F2` 切换平台。

## 后端选择

默认使用 **NeteaseCloudMusicApiEnhanced 4.41.1**。Python 客户端请求 API 服务，
由该服务处理网易云协议。交互式向导提供本机服务和已有服务两种配置：

```sh
uv run music-tui --setup-netease
uv run music-tui --netease-service  # 本机服务，保持此终端运行
```

另一个终端执行 `uv run music-tui`，按 `F2` 选择网易云 Enhanced；
或用 `uv run music-tui --source netease` 直接进入。
向导自动选择本机端口，保存 `enhanced.json` 中的 `apiUrl`。
配置目录为 `$XDG_CONFIG_HOME/music-tui/`，未设置时为 `~/.config/music-tui/`。
`--netease-api-url` 可覆盖已保存地址；未配置时地址为 `http://127.0.0.1:3000`。

官方后端保留为可选项，通过 HTTPS 直接请求网易云。
已有获批应用凭证和设备配置时，运行：

```sh
uv run music-tui --netease-backend official --setup-netease
uv run music-tui --source netease --netease-backend official
```

官方向导保存应用 ID、RSA 私钥和设备配置。
`NETEASE_APP_ID`、`NETEASE_PRIVATE_KEY_FILE`、`NETEASE_DEVICE_FILE` 可逐项覆盖。
设备参数和协议依据见[官方 API 接入依据](netease-official-api.md)。
官方匿名令牌与用户令牌分开管理；扫码后验证用户资料，令牌过期后重新扫码。
官方凭证单独存放于 `~/.local/share/music-tui/netease-official/`，权限为 `0600`。
官方资源 ID 保留字符串，不转换成 Enhanced 的数字 ID。

两个后端不共享凭证或资源 ID。网络、权限和接口失败直接报告，不会自动切换。
官方真实扫码和会员播放需获批应用凭证验证。

## Enhanced 接入依据

Enhanced 后端通过 `httpx.AsyncClient` 调用本机服务，不另行实现其加密协议。
接口兼容性取决于服务版本。以下服务配置和接口表仅适用于 Enhanced。

调研时，原 `mos9527/pyncm` 仓库、`greats3an/pyncm` 仓库和
[PyPI pyncm 元数据](https://pypi.org/pypi/pyncm/json)均返回 404。
仍可访问的 [zixing131 fork](https://github.com/zixing131/pyncm/blob/master/pyncm/apis/login.py)
仅提供早期手机号登录实现，未发现满足本次要求的二维码接口。
未找到可确认来源、维护状态和 API 的 `ncm-api-client` 网易云包，因此不添加此依赖。

增强版源码最近提交为 `2db6684453297ee86dec0492842f2320b08182d9`，日期为
2026-10-05。项目使用 MIT 许可证；README 要求 Node.js 22+。
`package.json` 的旧 `engines >=12` 与 README 不一致，安装时采用 README 要求。
依赖包含 axios、express、crypto-js、node-forge、qrcode，以及 unblockmusic-utils。
本项目仅调用常规网易云接口。[源码与许可证](https://github.com/NeteaseCloudMusicApiEnhanced/api-enhanced)
和 [包依赖](https://github.com/NeteaseCloudMusicApiEnhanced/api-enhanced/blob/main/package.json)。

## 本地服务

`uv run music-tui --netease-service` 使用向导保存的本机地址启动固定版本服务。
手动启动时可使用：

```sh
HOST=127.0.0.1 PORT=3000 ENABLE_GENERAL_UNBLOCK=false ENABLE_PROXY=false \
  npx --yes @neteasecloudmusicapienhanced/api@4.41.1
```

必须显式设置 `ENABLE_GENERAL_UNBLOCK=false`。
服务的全局解锁逻辑忽略请求中的 `unblock=false`，只检查环境变量。
Python 同时发送 `unblock="false"`，避免模块级解锁。
服务没有提供可供客户端核验此环境变量的接口，部署者必须按上面的命令关闭它。
不调用 `song/url/match`，不配置第三方音源。
[服务逻辑](https://github.com/NeteaseCloudMusicApiEnhanced/api-enhanced/blob/main/server.js)
和 [歌曲 URL 模块](https://github.com/NeteaseCloudMusicApiEnhanced/api-enhanced/blob/main/module/song_url_v1.js)。

## 请求与响应契约

下列接口均使用 POST JSON。每次请求带毫秒 `timestamp`，避免登录状态缓存。
`cookie` 放在请求体中，不放入 URL。服务地址优先读取命令行覆盖项，再读取向导配置；未配置时为 `http://127.0.0.1:3000`。
客户端设置 25 秒超时、`trust_env=False`，不读取环境代理。
错误提示只包含业务状态码，不展示响应正文或 Cookie。

| 接口 | 参数 | 使用的响应字段 |
| --- | --- | --- |
| `/login/qr/key` | 无 | `data.unikey` |
| `/login/qr/create` | `key`, `qrimg=true` | `data.qrimg` PNG data URL |
| `/login/qr/check` | `key` | `code`, `cookie` |
| `/user/account` | `cookie` | `profile.userId` 或 `account.id` |
| `/user/playlist` | `uid`, `limit=100`, `offset` | `playlist`, `more` |
| `/cloudsearch` | `keywords`, `type=1`, `limit=30`, `offset` | `result.songs`, `result.songCount` |
| `/playlist/detail` | `id` | `playlist.trackIds[].id` |
| `/song/detail` | 逗号分隔的 `ids` | `songs[]` |
| `/song/url/v1` | `id`, `level`, `unblock="false"` | `data[].id/url/code/level/freeTrialInfo` |

上述字段已对照维护者的 [接口源码](https://github.com/NeteaseCloudMusicApiEnhanced/api-enhanced/tree/main/module)
与 [调用文档](https://github.com/NeteaseCloudMusicApiEnhanced/api-enhanced/blob/main/public/docs/home.md)。
曲目名称为 `name`，歌手为 `ar[].name`，时长 `dt` 单位为毫秒。
歌单分两步加载：先保存完整 `trackIds`，再每页调用歌曲详情并按 ID 顺序排列。
不要依赖歌单详情中的 `tracks`，该字段可能只含部分歌曲。

## 扫码和播放

先获取 key，再获取二维码图片。每两秒轮询，最多三分钟。
状态 `800` 表示过期，`801` 等待扫码，`802` 等待确认，`803` 登录成功。
成功后解析 Cookie，要求存在 `MUSIC_U`，再调用账号接口验证。
验证后以原子替换方式保存 `netease-credential.json`，权限 `0600`。
恢复登录同样验证账号，不信任文件中的用户 ID。
[二维码模块](https://github.com/NeteaseCloudMusicApiEnhanced/api-enhanced/blob/main/module/login_qr_check.js)。

音质对应 `standard`、`exhigh`、`lossless`、`hires`、`jyeffect`、`vivid`、`jymaster`。
只接受 `code=200`、非空 URL、返回 level 与请求相同、`freeTrialInfo=null` 的歌曲。
会员权限不足、试听链接和服务端降级均视为该音质不可用。
不使用下载接口或外部音源作为回退。
[音质参数定义](https://github.com/NeteaseCloudMusicApiEnhanced/api-enhanced/blob/main/module/song_url_v1.js)。

扫码后的账号验证和会员播放需要真实账号，本次离线测试不能证明这些线上权限。

## Enhanced 历史验证

接入 Enhanced 时完整测试 53 项通过，包括两个平台的登录界面、网易云 HTTP 模拟、QQ 回归和真实 mpv。
Black、Ruff 及模块/函数 docstring 检查通过。
使用 Enhanced 4.41.1 本机服务实测了匿名搜索（30 条结果及总数）、PNG 二维码获取、
热歌榜分页（首 100 首、总计 200 首）。匿名歌曲接口返回的试听音源被正确排除。
手机确认后的真实账号恢复、个人歌单和会员完整播放未在本轮登录实测。


新增 Enhanced 向导与服务启动器后，完整测试 154 项通过。
使用隔离临时配置实测了自动选端口、`--netease-service` 启动固定版本服务、
匿名搜索（30 条结果及总数）和有效 PNG 二维码获取。测试服务已停止。
手机确认后的真实账号登录与会员播放仍需用户账号验证。
