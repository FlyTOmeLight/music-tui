# 网易云配置指南

Music TUI 中按 `F2` 可选择 QQ 音乐、网易云官方、网易云 Enhanced。配置完成后，日常切换不用退出应用。QQ 与两种网易云接入分别保存登录状态。

官方方式直接访问网易云，**不需要自建服务，但需要开发者应用凭证和设备配置**。Enhanced 需要本地 API 服务，不需要官方开发者应用。以下先说明官方方式。

## 1. 申请个人开发者身份

打开[个人入驻入口](https://developer.music.163.com/st/developer/apply/account?type=INDIVIDUAL)，登录网易云账号，填写信息并提交申请。

官方目前要求成年、实名。同一用户只能选择一种入驻身份；已经作为厂商入驻的账号不能再以个人身份入驻。申请结果以页面提示为准。[官方个人使用引导](https://developer.music.163.com/st/developer/document?docId=9504d35aa41a47c6ac9830b2dbf48f94)

入驻通过后进入应用管理，点击应用后面的「初始化」，再打开应用详情。记录其中的 `appId` 和 `privateKey`。个人流程的文档提供这两个值，不需要另外照搬厂商的公钥上传流程。应用详情也显示当天剩余接口调用次数。[官方使用引导](https://developer.music.163.com/st/developer/document?docId=9504d35aa41a47c6ac9830b2dbf48f94)

`appId` 是开发者应用 ID，不是网易云账号 UID。`privateKey` 是该应用的 RSA 私钥，不是密码、Cookie 或 `appSecret`。

## 2. 运行交互式配置

在项目目录执行：

```sh
uv run music-tui --setup-netease
```

先准备应用详情中的 `appId`、`privateKey`，以及网易云确认的四个设备字段。
向导逐项提问，私钥输入不会显示在终端。

| 需要填写 | 内容 |
| --- | --- |
| 应用 ID | 应用详情中的 `appId`，不是账号 UID。 |
| 私钥 | 粘贴平台提供的单行 Base64 私钥，或输入已有 PEM 私钥文件路径。多行 PEM 先保存为文件。 |
| `channel`、`os` | 网易云分配给应用的值。 |
| `deviceType`、`brand` | 网易云为该应用确认的设备值。 |

设备字段以应用接入资料为准；缺失时联系平台确认。
公开个人入驻说明没有提供桌面终端可直接套用的通用值，不要复制车机示例。
[官方公共参数](https://developer.music.163.com/st/developer/document?docId=0f7801d7d6d24180b8fc9058d1ffe593)

以下项目自动处理：

- 创建配置目录和文件，将私钥权限设为 `0600`。
- 生成符合要求的设备 ID，重新配置时保持原 ID。
- 读取客户端版本；检测系统版本、设备型号、IP 和网络类型。
- 系统版本、型号或网络信息无法可靠检测时，才要求填写对应字段。

`netStatus` 支持 `wifi`、`2g`、`3g`、`4g`、`5g`。其他网络类型需向网易云确认适用值。
向导不会申请开发者身份、初始化开放平台应用或获取接口授权。

默认保存到 `~/.config/music-tui/`，设置了 `XDG_CONFIG_HOME` 时则保存到
`$XDG_CONFIG_HOME/music-tui/`。应用 ID 与文件路径保存在 `netease.json`；
私钥和设备字段分别存入本机文件。后续启动自动读取，无需修改 `~/.zshrc`。
重新运行向导时，回车可保留已有非秘密设置和私钥，无需重复填写。

## 3. 启动并扫码登录

```sh
uv run music-tui
```

按 `F2` 选择网易云官方，再按 `l`，用网易云音乐手机 App 扫码并确认。
向导只完成应用配置；扫码才会登录听歌账号。成功后会显示个人歌单。
需要直接进入网易云时，执行 `uv run music-tui --source netease`。
配置修改后需重新启动 Music TUI；日常在界面内切换平台即可。

官方登录状态保存在 `~/.local/share/music-tui/netease-official/token.json`，支持 `XDG_DATA_HOME`。
令牌过期后按 `l` 重新扫码。真实官方扫码和会员音源仍需获批应用凭证验证。

## 手动配置与环境变量覆盖

已有配置文件时，也可在启动程序的同一终端设置以下变量。
每个变量分别优先于向导保存的对应设置，未设置的项仍读取本机配置。

```sh
export NETEASE_APP_ID="应用详情中的 appId"
export NETEASE_PRIVATE_KEY_FILE="$HOME/.config/music-tui/netease-private-key.pem"
export NETEASE_DEVICE_FILE="$HOME/.config/music-tui/netease-device.json"
uv run music-tui
```

私钥文件支持完整 PEM 或纯 Base64 内容。设备文件为 JSON 对象，包含
`channel`、`deviceId`、`deviceType`、`appVer`、`os`、`osVer`、`brand`、`model`、
`clientIp`、`netStatus` 十个非空字段。`deviceId` 仅含字母和数字，最多 64 位。
使用向导可省去手动创建这些文件。

## 常见错误

| 提示 | 处理方式 |
| --- | --- |
| 缺少官方配置 | 运行 `uv run music-tui --setup-netease`；若使用环境变量，检查其值是否覆盖了已保存配置。 |
| 无法读取私钥或设备配置 | 重新运行向导，检查文件路径和私钥完整性；手动 JSON 检查引号和逗号。 |
| 设备配置缺少公共参数 | 检查十个字段是否均为非空值；重新运行向导补齐。 |
| `deviceId` 格式错误 | 使用向导生成的 ID，不含连字符、空格或下划线。 |
| 应用未获此接口授权（300） | 向平台确认应用接口权限；重新扫码不能增加应用权限。 |
| 无版权或需要会员 / 购买 | 按应用及账号的实际播放权限选择歌曲和音质。 |

## 第二选择：Enhanced

安装 Node.js 22+。在单独终端运行服务并保持它运行：

```sh
HOST=127.0.0.1 PORT=3000 ENABLE_GENERAL_UNBLOCK=false ENABLE_PROXY=false \
  npx --yes @neteasecloudmusicapienhanced/api@4.41.1
```

在 Music TUI 内按 `F2` 选择网易云 Enhanced，再按 `l` 扫码。也可用以下命令直接进入：

```sh
uv run music-tui --source netease --netease-backend enhanced
```

服务地址默认 `http://127.0.0.1:3000`。如果端口已占用，修改服务的 `PORT`，并给 Music TUI 加 `--netease-api-url http://127.0.0.1:新端口`。官方配置失败时不会自动切换到 Enhanced。

实现与协议细节见[接入说明](netease-integration.md)和[官方接口依据](netease-official-api.md)。
