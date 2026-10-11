# 依赖与平台限制

## yt-dlp

独立安装最新版 yt-dlp；需要 Python 包时运行 `python3 -m pip install -U yt-dlp`。已有独立可执行文件时使用 `--yt-dlp /path/to/yt-dlp`。FFmpeg 必须可从 PATH 找到，或通过 `--ffmpeg-location` 指定可执行文件/目录。FFmpeg 负责分离音视频合并和 MP4 封装，不重新编码视频。

封装禁用用户 yt-dlp 配置、插件目录和磁盘缓存，不执行作品标题或描述，不接受任意附加命令。限制为 BiliBili、Douyin、XiaoHongShu 解析器，禁用通用网页解析路径。初始链接须为已支持的平台 HTTPS 地址，初始 DNS 拒绝私网；**yt-dlp 内部重定向和媒体网络请求由其自身处理**，本 CLI 不提供完整服务端网络沙箱。公开 HTTP 服务部署还需独立的出站网络隔离、鉴权和限流。

默认最高 1080p，优先 MP4 视频与 M4A 音频；兜底结果通过 FFmpeg 封装 MP4。`--no-playlist` 与元数据检查共同限制单视频，会员、私有、订阅限定、直播和待开播内容拒绝。

用户明确提供 Cookie 文件时才使用 `--cookies /private/cookies.txt`，不读取浏览器 Cookie。yt-dlp 可能维护该 Cookie 文件，应使用自己保管的专用副本，不在多人共享目录存储。Cookie 不是绕过付费和私有权限的授权。

解析失败时检查公开作品是否存在、分享查询参数是否完整、Cookie 是否有效以及 yt-dlp 是否需更新。脚本不会把原始 stderr 输出到公开 JSON，以免泄露临时签名地址或私人文件路径。

## 快手

短链接先在快手与 `chenzhongtech.com` 允许范围内解析跳转；最终链接必须提供匹配的作品编号与作者编号。再调用 `live.kuaishou.com/live_api/profile/feedbyid`，校验返回作品编号和 video 类型。只下载 `kwimgs.com`、`yximgs.com`、`kwaicdn.com`、`gifshow.com` 的 HTTPS 媒体，所有跳转和 DNS/IP 均校验。

这是公开接口，不保证匿名访问长期稳定。缺少编号、返回受限作品或 CDN 变化时返回错误，不使用他人登录态，也不自动回退为账号采集。

## 文件边界

下载临时目录与输出目录处于同一文件系统；完成大小、文件头、可用时的视频轨道验证和 SHA-256 后，通过不覆盖文件的硬链接原子发布。文件系统须支持硬链接（常见 ext4/APFS/NTFS 支持）；不支持时失败且清理临时文件。

直连下载在实际读取时执行字节限额，并核对 `Content-Length`；抖音还核对 DTK 已知字节数，防止预分配空洞和正常 EOF 被误报成功。yt-dlp 会做大小预检，封装按短间隔监测临时目录使用量，完成后再严格核对成品；监测并非操作系统硬配额。POSIX 超时会终止子进程组；Windows 终止 yt-dlp，FFmpeg 子进程的完整生命周期隔离须由宿主提供。

ffprobe 检查视频轨道，不做完整解码；没有 ffprobe 时只做文件头和大小检查，JSON 如实标记 `videoTrackVerified=false`。本 Skill 的限额和验证不应被称为完整内容安全扫描。
