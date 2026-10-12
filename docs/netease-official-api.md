# 网易云官方 API 接入依据

核实日期：2026-10-12。Music TUI 默认使用 NeteaseCloudMusicApiEnhanced；官方开放平台保留为需要应用授权的可选接入。两者认证、资源 ID 和返回结构不同，不应在单次请求失败后自动混用。

## 接入条件与来源

官方开放平台覆盖本项目所需的扫码登录、用户歌单、分页搜索、歌曲列表和播放地址。个人开发者入驻后需初始化应用，从应用详情取得 `appId` 和 `privateKey`。具体操作见[配置指南](netease-setup.md)。官方 CLI 也要求配置 `appId` 和 `privateKey`。[官方说明](https://github.com/NetEase/skills#前置条件)、[开发者入驻](https://developer.music.163.com/st/developer/apply/account?type=INDIVIDUAL)

本次以开放平台公开文档为协议依据。文档目录接口 `GET https://developer.music.163.com/api/openplatform/developer/api/doc/category` 返回目录及正文；单篇接口为 `GET /api/openplatform/developer/apidoc/get?docId=<id>`。

官方 npm 包 `@music163/ncm-cli@0.1.7` 的包元数据声明 MIT、Node.js >=18。0.1.0 和 0.1.7 的发布代码均已混淆，因此不把猜测的内部实现作为 Python 接入契约。无需安装该 CLI 即可通过 HTTPS 调用官方接口。[npm 元数据](https://registry.npmjs.org/@music163%2fncm-cli/0.1.7)

## 请求签名与设备信息

请求域名为 `https://openapi.music.163.com`。公共参数包括 `appId`、`signType=RSA_SHA256`、毫秒时间戳 `timestamp`、JSON 字符串 `bizContent` 和 `device`。按接口需要发送 `accessToken`。业务参数全部放入 `bizContent`。[访问方式](https://developer.music.163.com/st/developer/document?docId=d5ab035d4b9d4a088ff9f4ef3ea5005a)

签名时排除 `sign` 和空字符串参数，将其余键按 ASCII 顺序排列，连接为 `key=value&key=value`。对尚未 URL 编码的字符串执行 RSA PKCS#1 v1.5 / SHA-256 签名，再 Base64 编码。发送请求时由 HTTP 客户端编码参数，不能先编码后签名。[官方 Python 签名示例](https://developer.music.163.com/st/developer/document?docId=62d2c09579c241fa966de6fbd739d574)

`device` 必填字段为 `channel`、`deviceId`、`deviceType`、`appVer`、`os`、`osVer`、`brand`、`model`、`clientIp`、`netStatus`。其中 `channel`、`deviceType`、`os`、`brand` 需按应用接入资料配置。`deviceId` 必须稳定、仅含数字或字母且不超过 64 字符；`appVer` 为三段数字。不要直接复制官方示例的设备身份。[公共参数](https://developer.music.163.com/st/developer/document?docId=0f7801d7d6d24180b8fc9058d1ffe593)

## 认证

以下路径均相对于官方请求域名，响应字段均位于 `data` 下。

| 操作 | 路径 | 业务参数及返回值 |
| --- | --- | --- |
| 匿名令牌 | `/openapi/music/basic/oauth2/login/anonymous` | `clientId=appId`；返回 `accessToken`，与设备 ID 绑定，应缓存。 |
| 二维码 | `/openapi/music/basic/user/oauth2/qrcodekey/get/v2` | `type=2, expiredKey="300"`；返回 `qrCodeUrl, uniKey`。二维码有效 5 分钟。 |
| 轮询授权 | `/openapi/music/basic/oauth2/device/login/qrcode/get` | `key=uniKey, clientId=appId`；必须用匿名令牌。`status` 为 800 过期、801 等待、802 授权中、803 成功、804 错误。 |
| 用户资料 | `/openapi/music/basic/user/profile/get/v2` | 无业务参数；返回 `id, nickname` 等字段。 |
| 刷新令牌 | `/openapi/music/basic/user/oauth2/token/refresh/v2` | `clientId=appId, clientSecret=appSecret, refreshToken`；返回新的 `accessToken, refreshToken, expiresTime`。 |

扫码成功时令牌对象位于 `data.accessToken`，其中包含 `accessToken`、`refreshToken`、`expireTime`。`expireTime` 为秒数寿命，不是 Unix 时间戳。刷新接口字段拼写为 `expiresTime`。访问令牌默认 7 天，刷新令牌默认 20 天。错误 `1406` 可尝试刷新，`1407/1408` 要重新登录。刷新所需 `appSecret` 与 RSA 私钥不同；未配置它时应引导重新扫码，不猜测其值。[匿名登录](https://developer.music.163.com/st/developer/document?docId=1a5fb2c7b30b44609fa81129a8e1908d)、[二维码](https://developer.music.163.com/st/developer/document?docId=2bb12a93e71a4be0842243b930c2f33c)、[轮询](https://developer.music.163.com/st/developer/document?docId=4d0301c68c5d45d8811d7787ee37d2f6)、[资料](https://developer.music.163.com/st/developer/document?docId=8ed9b2f123e44923979596a277733421)、[刷新](https://developer.music.163.com/st/developer/document?docId=2066d57bbed2445baeb18429e44b8689)

## 歌单、搜索和播放

| 操作 | 路径 | 请求与返回 |
| --- | --- | --- |
| 自建歌单 | `/openapi/music/basic/playlist/created/get/v2` | `limit, offset`；`data.records, recordCount`。列表末尾包含红心歌单。 |
| 收藏歌单 | `/openapi/music/basic/playlist/subed/get/v2` | `limit, offset`；`data.records, recordCount`。 |
| 歌单歌曲 | `/openapi/music/basic/playlist/song/list/get/v5` | `playlistId, limit, offset, qualityFlag`；`data.tracks, trackCount`。 |
| 歌曲搜索 | `/openapi/music/basic/search/song/get/v3` | `keyword, limit, offset, qualityFlag`；`data.records, recordCount`。 |
| 歌曲详情 | `/openapi/music/basic/song/detail/get/v2` | `songId, withUrl`，可选 `level, bitrate`；`data.playUrl, level, br, freeTrail` 等。 |
| 音质权限 | `/openapi/music/basic/song/music/quality/sound/sp/get` | `songId`；各音质条目的 `sqPrivilege.playFlag` 表示当前用户可播权限。 |
| 播放地址 | `/openapi/music/basic/song/playurl/get/v2` | `songId`，可选 `level, bitrate`；`data.url, level, br, freeTrail`。 |

歌单分页 `limit` 最大 500。歌曲使用加密字符串 `id`；不能转换为整数，也不能直接替换为网页版数字 ID。列表项含 `name`、毫秒 `duration`、`artists`、`fullArtists`、`album`、`visible`、`playFlag`。`visible=false` 表示无版权。歌单接口返回 `code=200, subCode="10007", data=null` 时，可能是空歌单、超出末页或无权访问。[自建歌单](https://developer.music.163.com/st/developer/document?docId=e4fef4e5cc564fc1adbcfcf02140f0d5)、[收藏歌单](https://developer.music.163.com/st/developer/document?docId=1b3d86a47f2e45c7bd631bcd26052382)、[歌单歌曲](https://developer.music.163.com/st/developer/document?docId=200b1217d6634259b3d665e330bd7dd0)、[歌曲搜索](https://developer.music.163.com/st/developer/document?docId=b175e0d52550427cbb7cd4735a9de765)

音质 `level` 包括 `standard`、`exhigh`、`lossless`、`hires`、`jyeffect`、`jymaster`、`sky`、`vivid`。标准、极高、无损、Hi-Res 的 `bitrate` 请求值分别为 128、320、999、1999，不是返回 `br` 的单位。高音质需应用权限。服务端可能降级；本项目拒绝与所选档位不符的音频，提示用户更换音质。`freeTrail` 非空表示试听片段。播放接口即使 `code=200`，仍可能返回 `subCode=10003`（当前端无版权）或 `10004`（需购买）及空 URL；必须展示失败原因。[歌曲详情](https://developer.music.163.com/st/developer/document?docId=2f583c5e2d764bbabaa221865f62dbc4)、[播放地址](https://developer.music.163.com/st/developer/document?docId=3d2c9f695ff24f4ea37611614b7f7856)

音质权限响应通过 `jyMasterMusic`、`vividMusic`、`jyEffectMusic`、`hrMusic`、`sqMusic`、`hmusic`、`lmusic` 对应本项目的七个档位。先确认歌曲 `visible` 和 `playFlag`，再按每档 `sqPrivilege.playFlag` 展示可选音质。[新版音质权限](https://developer.music.163.com/st/developer/document?docId=548179f7ecbc417d8acd35d604aea3c9)

## 验证边界

已核实公开文档和官方发布包。尚未使用获批应用凭证执行真实登录、刷新和会员播放。文档能够支持原生 Python 接入，但应用可用接口和曲库范围仍由开放平台授权决定。
