# 网易云配置指南

Music TUI 中按 `F2` 可选择 QQ 音乐、网易云官方、网易云 Enhanced。配置完成后，日常切换不用退出应用。QQ 与两种网易云接入分别保存登录状态。

官方方式直接访问网易云，**不需要自建服务，但需要开发者应用凭证和设备配置**。Enhanced 需要本地 API 服务，不需要官方开发者应用。以下先说明官方方式。

## 1. 申请个人开发者身份

打开[个人入驻入口](https://developer.music.163.com/st/developer/apply/account?type=INDIVIDUAL)，登录网易云账号，填写信息并提交申请。

官方目前要求成年、实名。同一用户只能选择一种入驻身份；已经作为厂商入驻的账号不能再以个人身份入驻。申请结果以页面提示为准。[官方个人使用引导](https://developer.music.163.com/st/developer/document?docId=9504d35aa41a47c6ac9830b2dbf48f94)

入驻通过后进入应用管理，点击应用后面的「初始化」，再打开应用详情。记录其中的 `appId` 和 `privateKey`。个人流程的文档提供这两个值，不需要另外照搬厂商的公钥上传流程。应用详情也显示当天剩余接口调用次数。[官方使用引导](https://developer.music.163.com/st/developer/document?docId=9504d35aa41a47c6ac9830b2dbf48f94)

`appId` 是开发者应用 ID，不是网易云账号 UID。`privateKey` 是该应用的 RSA 私钥，不是密码、Cookie 或 `appSecret`。

## 2. 保存应用私钥

在终端创建应用配置目录，然后打开私钥文件：

```sh
mkdir -p "$HOME/.config/music-tui"
chmod 700 "$HOME/.config/music-tui"
nano "$HOME/.config/music-tui/netease-private-key.pem"
```

粘贴应用详情中的完整 `privateKey` 并保存。支持带 `-----BEGIN PRIVATE KEY-----` / `-----END PRIVATE KEY-----` 的 PEM 内容，也支持平台提供的纯 Base64 私钥。不要给内容加引号，也不要粘贴整个 JSON 对象。文件扩展名不影响读取。

```sh
chmod 600 "$HOME/.config/music-tui/netease-private-key.pem"
```

私钥保存在上面的本机目录，不要放进项目仓库。Music TUI 使用它签名请求，无需安装官方 `ncm-cli`。

## 3. 确认设备参数并创建 JSON

**这一步不能仅凭 `appId` 和私钥自动完成。** 当前客户端采用官方公共参数中的 `device` 结构。公开文档将其标为 IOT 端参数，并注明移动端 App 亦可；其中四个字段需要由网易云分配或确认。公开个人入驻说明没有提供桌面终端可直接套用的通用值。[官方公共参数](https://developer.music.163.com/st/developer/document?docId=0f7801d7d6d24180b8fc9058d1ffe593)

| 字段 | 从哪里取得 / 怎样填写 |
| --- | --- |
| `channel`、`os` | 网易云分配给应用的值；按应用接入资料填写，缺失时联系平台确认。 |
| `deviceType`、`brand` | 由网易云确认；不要直接复制文档里的车机示例。 |
| `deviceId` | 本机生成一次，长期保持不变。仅限字母和数字，最多 64 位。 |
| `appVer` | 本客户端版本，当前为 `0.1.0`；格式为三段数字。 |
| `osVer`、`model` | 实际系统版本与设备型号。 |
| `clientIp` | 设备调用接口时的 IP，按应用接入要求填写。 |
| `netStatus` | 实际网络类型：`wifi`、`2g`、`3g`、`4g` 或 `5g`。其他网络类型向平台确认。 |

生成设备 ID；复制输出，后续不要反复生成：

```sh
uv run python -c 'import uuid; print(uuid.uuid4().hex)'
nano "$HOME/.config/music-tui/netease-device.json"
```

将以下 JSON 粘贴到文件。**把所有 `REPLACE_...` 替换为实际值**；示例中的 `wifi` 也应与实际网络一致：

```json
{
  "channel": "REPLACE_WITH_ASSIGNED_CHANNEL",
  "deviceId": "REPLACE_WITH_GENERATED_DEVICE_ID",
  "deviceType": "REPLACE_WITH_CONFIRMED_DEVICE_TYPE",
  "appVer": "0.1.0",
  "os": "REPLACE_WITH_ASSIGNED_OS",
  "osVer": "REPLACE_WITH_SYSTEM_VERSION",
  "brand": "REPLACE_WITH_CONFIRMED_BRAND",
  "model": "REPLACE_WITH_DEVICE_MODEL",
  "clientIp": "REPLACE_WITH_CLIENT_IP",
  "netStatus": "wifi"
}
```

拿不到这些参数时，当前原生官方方式尚不能完成配置。可使用文末 Enhanced 方式，不要猜测设备身份。官方入口有无权限、个人应用能否使用所需接口，最终以平台授权为准。

## 4. 启动并扫码登录

在项目目录、同一个终端执行：

```sh
export NETEASE_APP_ID="替换为应用详情中的 appId"
export NETEASE_PRIVATE_KEY_FILE="$HOME/.config/music-tui/netease-private-key.pem"
export NETEASE_DEVICE_FILE="$HOME/.config/music-tui/netease-device.json"
uv run music-tui
```

按 `F2`，选择网易云官方；按 `l` 显示二维码，用网易云音乐手机 App 扫码并确认。成功后会显示个人歌单。需要直接进入网易云时，启动命令加 `--source netease`。

上面的 `export` 仅影响当前终端及其启动的进程。确认可用后，可将这三行加入 `~/.zshrc`，让新终端自动读取。已经运行的 Music TUI 不会读取后来修改的环境变量，需要重启一次。私钥和设备文件只需准备一次；平时在界面内切换即可。

官方登录状态保存在 `~/.local/share/music-tui/netease-official/token.json`，支持 `XDG_DATA_HOME`。令牌过期后按 `l` 重新扫码。已实现离线验证；真实官方扫码和会员音源仍需获批应用凭证验证。

## 常见错误

| 提示 | 处理方式 |
| --- | --- |
| 需要三个 `NETEASE_*` 配置 | 检查是否在启动程序的同一终端执行了三条 `export`。路径须指向实际文件。 |
| 无法读取私钥或设备配置 | 检查私钥是否完整，JSON 是否有漏引号、多余逗号；不要把文件路径写成文件内容。 |
| 设备配置缺少公共参数 | 对照上面的十个字段；空值不被接受。 |
| `deviceId` 格式错误 | 使用生成的 32 位 ID，不含连字符、空格或下划线。 |
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
