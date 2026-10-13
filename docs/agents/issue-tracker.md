# 任务跟踪：GitHub

规格与任务发布到 `FlyTOmeLight/music-tui` 的 GitHub Issues，使用 `gh` 操作。

- 发布规格或任务：每份规格或任务对应一个 Issue；使用正文文件与 `--body-file` 保留换行，并附上 `ready-for-agent` 标签。
- 读取任务：读取完整正文、标签和评论后再执行。
- 拆分任务：按依赖顺序发布；优先使用 GitHub 原生阻塞关系与子任务关系，不支持时在正文列出 `Blocked by`。
- 标签按 `triage-labels.md` 映射。仅在当前任务需要时创建缺失的标签，不修改已有标签。
- PRs as a request surface: no.

本配置只定义操作方式；评论、通知及其他对外写入仍需当前任务授权。
