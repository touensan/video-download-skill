# 视频解析下载 Skill

从域动工坊的视频工具提炼的独立 AI Agent Skill，**MIT 开源**。支持抖音、哔哩哔哩、小红书和快手的公开单视频：解析分享文本、提取文案、下载 MP4，并返回本地文件、字节数和 SHA-256。

不需要域动工坊账号、Go 后端、数据库或本项目的生产配置。只开源视频解析下载；其他工具不在此仓库。

## 安装

Python 3.10+；哔哩哔哩、小红书及抖音 yt-dlp 路径需要独立安装 [yt-dlp](https://github.com/yt-dlp/yt-dlp) 和 [FFmpeg](https://ffmpeg.org/download.html)。快手与已配置的抖音 DTK 路径只需要 Python 标准库，推荐安装 ffprobe 以验证视频轨道。

把本仓库克隆到 Agent 的 Skill 目录，例如 Codex：

```bash
git clone https://github.com/touensan/video-download-skill.git ~/.codex/skills/video-download
python3 -m pip install -U yt-dlp
cd ~/.codex/skills/video-download
python3 scripts/video.py doctor
```

其他支持 `SKILL.md` 的 Agent 可直接使用这个目录。已有同名目录时先检查其内容，不要覆盖。

在聊天中使用 `$video-download` 并提供分享文本，例如“解析这个公开视频，下载 MP4 并把文件发给我”。

## 命令行

```bash
# 只解析，不下载视频
python3 scripts/video.py parse 'https://www.douyin.com/video/作品编号'

# 推荐用 UTF-8 文件传入原始分享文本
python3 scripts/video.py download --input-file /path/to/share.txt --output-dir ./downloads

# 根据自己的需求调整成品上限和清晰度
python3 scripts/video.py download --input-file /path/to/share.txt --max-mib 200 --max-height 720

# 仅在需要时提供本人授权的 Netscape Cookie 文件
python3 scripts/video.py parse --input-file /path/to/share.txt --cookies /private/cookies.txt
```

示例中的作品编号和路径须替换为实际值。所有参数见 `python3 scripts/video.py --help`。

默认成品上限 **95 MiB**、最高 **1080p**、解析超时 **45 秒**、下载超时 **180 秒**。这是工具默认值，不是平台上限。yt-dlp 在合并期间允许有限的额外临时空间，成品仍严格复核限额。输出文件不覆盖已有文件，失败时清理本次临时目录。

成功时 stdout 返回 JSON；错误写入 stderr，退出码为 1。`download` 返回绝对 `file` 路径、`sizeBytes`、`sha256` 和 `videoTrackVerified`。没有 ffprobe 时只检查 MP4 文件头和大小，`videoTrackVerified` 为 `false`，不代表完整解码验收。

## 平台与依赖

| 平台 | 默认路径 | 使用条件 |
| --- | --- | --- |
| 抖音 | 配有 `DTK_BASE_URL` 时走 DTK，否则 yt-dlp | DTK 返回完整文案和已确认无水印的 MP4；yt-dlp 可能需要本人 Cookie |
| 哔哩哔哩 | yt-dlp + FFmpeg | 公开单视频；不解锁会员或付费内容 |
| 小红书 | yt-dlp + FFmpeg | 视频笔记，分享链接可能须保留原查询参数；不支持图集 |
| 快手 | Python 匿名作品 API | 不需要项目账号或 Cookie；公开接口变化时明确报错 |

抖音 DTK 配置与异步任务处理见 [references/dtk.md](references/dtk.md)，其他依赖和排错见 [references/backends.md](references/backends.md)。脚本不会启动服务、安装证书、读取浏览器 Cookie 或创建平台身份。视频号、直播、图集、账号批量采集和 DRM 解锁不支持。

`watermark: null` 表示未知；只有 DTK 返回明确无水印标记的候选才返回 `false`。没有通用的“去水印”图像处理，也不承诺每个链接都能解析。签名媒体 URL 有效期由平台决定。

## 来源、许可和验证

功能与约束来自 [ruoyi-go-by](https://github.com/touensan/ruoyi-go-by) 的 `app/videodownload`、`app/douyin` 和 [ruoyi-uniapp-by](https://github.com/touensan/ruoyi-uniapp-by) 视频工具。此仓库将其重新整理为可独立运行的 Python 脚本，没有复制项目账号、服务配置或私有数据，也不发布整个应用。

本仓库脚本和文档采用 [MIT](LICENSE)。外部程序保留自己的许可，详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。MIT 允许商业使用，媒体内容及平台访问权限由各自权利人决定。

```bash
python3 -m unittest discover -s tests -v
```

自动化测试使用合成响应与临时文件，不需要凭据或联网。实际平台可能改变反爬、Cookie、分享跳转和解析规则，具体首版实测结果见 [references/validation.md](references/validation.md)。
