# QQ 歌手热门／最新接口验证

已在 2026-10-14 使用仓库锁定的 qqmusic-api-python 0.7.3 联网读取 QQ 公共歌手元数据。探针不加载账号凭证、不修改喜欢或歌单。

## 已验证接口

沿用 SDK 的 `musichall.song_list_server.GetSingerSongList` 请求及其签名、传输、响应模型。请求参数为：

- `singerMid`：准确的歌手 MID。
- `order=1`：上游热门顺序；`order=2`：发行日期降序的最新顺序。
- `num`：本次请求数量。
- `begin`：零起始偏移量，分页时为 `(page - 1) * page_size`。

响应返回 `totalNum` 与 `songList[*].songInfo`，后者包含歌曲 MID、姓名、歌手身份及发行日期。

SDK 的 `get_songs_list` 把数量写成 `number`。实测 `number=10` 被忽略，返回默认 30 首；若仍按 10 首推进偏移会重复。生产适配使用 `dataclasses.replace` 调整请求参数为 `num`，保留其余 SDK 请求执行行为。

## 真实响应证据

每个排序分别请求 `num=10,begin=0`、`num=10,begin=10` 和 `num=20,begin=0`。两个小页顺序拼接与大页歌曲 MID 顺序完全相等，两页之间没有重复，两个排序均返回相同歌手总数。

| 歌手 | MID | 总数 | 热门第 1 页开头 | 热门第 2 页开头 | 最新前 20 首日期范围 |
|---|---|---:|---|---|---|
| 周杰伦 | `0025NhlN2yWrP4` | 1011 | 晴天 `0039MnYb0qxYhV`、搁浅 `001Bbywq2gicae` | 发如雪 `0027oMO61wWi55`、花海 `003cI52o4daJJL` | 2026-03-25 至 2023-12-22 |
| 李荣浩 | `000aHmbL2aPXWH` | 394 | 恋人 `001auUcH4WQs2V`、不将就 `000edAg12jLBrN` | 李白 `000rMFLS0ZnngN`、女儿国 `003YYX4K3gTZBT` | 2024-10-18 至 2022-12-21 |

另按生产页大小对周杰伦请求两页各 30 首，并与前 60 首比较：热门、最新均拼接一致、两页无重复、实际返回 30／30 首；最新前 60 首日期仍非递增。

两个歌手最新前 20 首的发行日期均为非递增；热门顺序与最新顺序不同，且不按发行日期排序。热门使用上游排序，不自行计算热度；响应没有返回可独立核算的热度分值。

周杰伦最新第 1 页末尾是《湘女多情》`0007XPND1Mf9dN`（2026-03-25）；第 2 页从《那天下雨了》`0044SaFh0apuR2`（同日）接续，随后日期下降至《圣诞星》2023-12-22。

详情使用 SDK `get_desc([mid])`，实际返回对应歌手的 `basic_info.name/mid/has_photo`、`ex_info.desc` 和图片字段；简介、图片可以缺失，不影响身份和歌曲浏览。

## 复现与证据边界

在已同步仓库依赖的环境运行：

```sh
uv run python scripts/probe_qq_artist.py 0025NhlN2yWrP4
uv run python scripts/probe_qq_artist.py 000aHmbL2aPXWH
```

脚本输出公共歌曲身份、发行日期和分页比对结果。两页加较大前缀的一致性验证了上游先确定顺序再按偏移切片，不能保证上游榜单在长时间播放期间永远不变。总数和榜单可能随上游更新；本次没有逐页遍历全部 1011／394 首，也未证明 QQ 热度计算算法。离线测试验证应用契约，以上联网结果验证接口能力，两者分别记录。

SDK 源码参考：[qqmusic-api-python 项目](https://github.com/l-1124/QQMusicApi)。已安装版本源码是本次请求构造的依据；真实 QQ 响应是排序和分页结论的依据。
