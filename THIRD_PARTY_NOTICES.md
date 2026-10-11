# 外部依赖与来源

本仓库的 Skill、Python 封装、测试与文档为独立整理实现，采用 MIT。未把以下第三方源码、二进制或凭据打包进仓库，也未将第三方软件的许可替换为 MIT。

| 项目 | 用途 | 许可来源 |
| --- | --- | --- |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | 独立安装并通过子进程解析/下载公开单视频 | [源码 Unlicense](https://github.com/yt-dlp/yt-dlp/blob/master/LICENSE)；打包版本及附带依赖以其具体分发说明为准 |
| [Douyin_TikTok_Download_API / DTK](https://github.com/Evil0ctal/Douyin_TikTok_Download_API) | 可选：调用安装者配置的 v5 HTTP API | [Apache-2.0](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/LICENSE) |
| [FFmpeg / ffprobe](https://ffmpeg.org/) | 可选外部工具：合并、封装与视频轨道验证 | [LGPL / GPL，取决于安装者选用的构建](https://ffmpeg.org/legal.html) |

功能来源为 touensan 的域动工坊项目；本次提炼对应本地 `app/videodownload/{service,kuaishou}.go`、`app/douyin/{service,safety}.go` 和前端视频工具。其完整应用及历史 Git 版本并未随本仓库发布。公开来源链接见根 README。

没有复用 `KS-Downloader`（GPL-3.0）、`wx_channels_download`（带 Commons Clause 附加条款）或 `res-downloader` 的代码。快手实现按项目现有公开作品接口逻辑独立整理；视频号未接入。
