#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Standalone YDGF public-video skill; stdlib + optional yt-dlp/FFmpeg."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import http.client
import importlib.util
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import time
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit
import uuid

MIB = 1024 * 1024
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128 Safari/537.36"
PLATFORMS = {
    "bilibili": ("bilibili.com", "b23.tv"),
    "douyin": ("douyin.com", "iesdouyin.com"),
    "xiaohongshu": ("xiaohongshu.com", "xhslink.com"),
    "kuaishou": ("kuaishou.com", "kuaishou.cn", "chenzhongtech.com"),
}
KS_MEDIA = ("kwimgs.com", "yximgs.com", "kwaicdn.com", "gifshow.com")
DY_MEDIA = ("douyinvod.com", "zjcdn.com", "bytecdn.cn", "douyin.com")
FORMAT = "bv*[height<={h}][ext=mp4]+ba[ext=m4a]/b[height<={h}][ext=mp4]/best[height<={h}]"


class VideoError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def matches(host, suffixes):
    return any(host == suffix or host.endswith("." + suffix) for suffix in suffixes)


def https_url(raw, suffixes=None):
    if not isinstance(raw, str) or len(raw) > 8192 or re.search(r"[\s\\\x00-\x1f\x7f]", raw):
        raise VideoError("unsafe_url", "链接格式无效")
    try:
        u = urlsplit(raw)
        if (u.scheme != "https" or not u.hostname or u.username is not None
                or u.password is not None or u.port not in (None, 443) or not u.path):
            raise ValueError()
        if suffixes is not None and not matches(u.hostname.lower(), suffixes):
            raise ValueError()
        return u
    except ValueError:
        raise VideoError("unsafe_url", "仅接受受支持域名的 HTTPS 链接") from None


def extract(text):
    if len(text) > 2048:
        raise VideoError("invalid_input", "分享文本最多 2048 字")
    links = re.findall(r"https?://[^\s<>\"'，。；！、]+", text, re.I)
    if len(links) != 1:
        raise VideoError("invalid_input", "请提供唯一一个公开单视频链接")
    raw = links[0].rstrip("。，、；;！!？?)]}）】")
    u = https_url(raw)
    for platform, suffixes in PLATFORMS.items():
        if matches(u.hostname.lower(), suffixes):
            if platform == "kuaishou" and matches(u.hostname.lower(), ("chenzhongtech.com",)):
                kuaishou_parts(raw)
            elif platform == "douyin" and not (
                re.fullmatch(r"/(?:video/|share/video/)\d{19}/?", u.path)
                or (u.hostname.lower() == "v.douyin.com" and re.fullmatch(r"/[\w-]{3,64}/?", u.path, re.ASCII))
            ):
                raise VideoError("invalid_input", "请提供抖音单视频分享链接")
            return urlunsplit(u._replace(fragment="")), platform
    raise VideoError("unsupported_platform", "支持抖音、哔哩哔哩、小红书和快手；视频号未接入")


def public_addresses(host):
    blocked = [ipaddress.ip_network(x) for x in (
        "192.0.0.0/24", "192.88.99.0/24", "2001::/23", "2002::/16", "3fff::/20"
    )]
    addresses = list(dict.fromkeys(x[4][0] for x in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)))
    if not addresses:
        raise VideoError("unsafe_network", "域名没有可用公网地址")
    for value in addresses:
        ip = ipaddress.ip_address(value)
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if (not ip.is_global or any(ip in net for net in blocked)
                or (ip.version == 6 and ip not in ipaddress.ip_network("2000::/3"))):
            raise VideoError("unsafe_network", "拒绝私网或特殊用途地址")
    return addresses


class PublicHTTPS(http.client.HTTPSConnection):
    """Pin a validated public IP while preserving hostname verification/SNI."""
    def connect(self):
        last = None
        for ip in public_addresses(self.host):
            sock = None
            try:
                sock = socket.create_connection((ip, 443), self.timeout)
                self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
                return
            except OSError as exc:
                if sock is not None:
                    sock.close()
                last = exc
        raise last or VideoError("network_error", "连接失败")


@contextmanager
def public_get(raw, suffixes, referer=None, timeout=20):
    for _ in range(5):
        u = https_url(raw, suffixes)
        conn = PublicHTTPS(u.hostname, timeout=timeout, context=ssl.create_default_context())
        try:
            headers = {"User-Agent": UA, "Accept-Encoding": "identity"}
            if referer:
                headers["Referer"] = referer
            conn.request("GET", urlunsplit(("", "", u.path, u.query, "")), headers=headers)
            response = conn.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                raw = urljoin(raw, response.getheader("Location", ""))
                https_url(raw, suffixes)
                continue
            if response.status != 200:
                raise VideoError("upstream_http", f"来源返回 HTTP {response.status}")
            yield response, raw
            return
        finally:
            conn.close()
    raise VideoError("unsafe_redirect", "来源跳转次数过多")


def bounded_json(response, maximum=2 * MIB):
    raw = response.read(maximum + 1)
    if len(raw) > maximum:
        raise VideoError("invalid_response", "来源响应过大")
    try:
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, UnicodeError):
        raise VideoError("invalid_response", "来源未返回有效 JSON 对象") from None


def dtk_base(raw):
    try:
        u = urlsplit(raw)
        if not u.hostname or u.username is not None or u.password is not None or u.query or u.fragment:
            raise ValueError()
        if u.scheme != "https" and not (u.scheme == "http" and u.hostname in ("127.0.0.1", "localhost", "::1")):
            raise ValueError()
        _ = u.port
        return u
    except ValueError:
        raise VideoError("dtk_config", "DTK_BASE_URL 须为 HTTPS 地址或 HTTP 本机回环地址") from None


def dtk_call(base, key, path, body=None, timeout=30):
    u = dtk_base(base)
    cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
    conn = cls(u.hostname, port=u.port, timeout=timeout)
    headers = {"Content-Type": "application/json", "X-API-Key": key}
    try:
        conn.request("POST" if body is not None else "GET", u.path.rstrip("/") + "/api/v1/" + path,
                     body=json.dumps(body).encode() if body is not None else None, headers=headers)
        response = conn.getresponse()
        if response.status not in (200, 202):
            raise VideoError("dtk_http", f"DTK 返回 HTTP {response.status}，请检查服务、权限或平台状态")
        value = bounded_json(response)
        if value.get("success") is not True or not isinstance(value.get("data"), dict):
            raise VideoError("dtk_response", "DTK 未返回成功结果")
        return value["data"], response.status
    finally:
        conn.close()


def dtk_key():
    key = os.environ.get("DTK_API_KEY", "")
    key_file = os.environ.get("DTK_API_KEY_FILE")
    if key_file:
        with open(Path(key_file).expanduser(), "r", encoding="utf-8") as f:
            key = f.read(16385).strip()
    if not key or len(key) > 16384 or "\n" in key or "\r" in key:
        raise VideoError("dtk_config", "请配置 DTK_API_KEY 或 DTK_API_KEY_FILE；不要将密钥写入仓库")
    return key


def parse_dtk(source, args):
    base, key = os.environ.get("DTK_BASE_URL", ""), dtk_key()
    deadline = time.monotonic() + args.parse_timeout
    data, status = dtk_call(base, key, "parse?wait=20", {"url": source, "include_raw": False},
                            timeout=min(30, args.parse_timeout))
    if status == 202:
        task = data.get("task_id", "")
        if not isinstance(task, str) or not re.fullmatch(r"[0-9a-f-]{36}", task):
            raise VideoError("dtk_response", "DTK 任务编号无效")
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise VideoError("parse_timeout", "DTK 解析超时，请稍后重试")
            time.sleep(min(0.5, remaining))
            data, _ = dtk_call(base, key, "tasks/" + task, timeout=min(20, remaining))
            if data.get("state") == "failed":
                raise VideoError("dtk_task_failed", "DTK 解析任务失败；查看安装者自己的服务诊断")
            if data.get("state") == "done":
                data = data.get("data")
                break
    if (not isinstance(data, dict) or data.get("platform") != "douyin" or data.get("kind") != "video"
            or data.get("is_private") or data.get("is_deleted") or data.get("is_live")
            or not re.fullmatch(r"\d{19}", str(data.get("content_id", "")))):
        raise VideoError("unsupported_content", "来源未返回可公开下载的抖音单视频")
    content_id = data["content_id"]
    expected = re.search(r"/(?:video|share/video)/(\d{19})", urlsplit(source).path)
    if expected and expected[1] != content_id:
        raise VideoError("invalid_response", "解析结果与输入作品不一致")
    description = data.get("description")
    if not isinstance(description, str) or len(description) > 64000:
        raise VideoError("invalid_response", "来源没有返回完整文案")
    media = data.get("media") or {}
    candidates = []
    for item in media.get("streams") or []:
        if not isinstance(item, dict) or item.get("format") != "mp4" or item.get("watermark") is not False:
            continue
        size = item.get("size_bytes")
        if not isinstance(size, int) or not 0 < size <= args.max_bytes:
            continue
        if not all(isinstance(item.get(dim), int) and item[dim] > 0 for dim in ("width", "height")):
            continue
        if item["height"] > args.max_height:
            continue
        try:
            https_url(item.get("url"), DY_MEDIA)
        except VideoError:
            continue
        candidates.append(item)
    # Preserve the project's preference for the smallest verified mobile MP4.
    chosen = min(candidates, key=lambda x: x["size_bytes"]) if candidates else None
    return {"platform": "douyin", "backend": "dtk", "id": content_id, "sourceUrl": source,
            "title": str(data.get("title", ""))[:300], "author": str((data.get("author") or {}).get("nickname", ""))[:120],
            "description": description, "durationSeconds": (data.get("duration_ms") or 0) / 1000,
            "watermark": False if chosen else None, "media": [chosen] if chosen else []}


def kuaishou_parts(raw):
    u = https_url(raw, PLATFORMS["kuaishou"])
    params = parse_qs(u.query)
    match = re.fullmatch(r"/short-video/([A-Za-z0-9_-]{1,128})/?", u.path)
    if matches(u.hostname.lower(), ("chenzhongtech.com",)):
        match = re.fullmatch(r"/fw/photo/([A-Za-z0-9_-]{1,128})", u.path)
        if not match or params.get("photoId", [""])[0] != match[1]:
            raise VideoError("invalid_input", "快手分享链接作品编号不一致")
    author = params.get("authorId", params.get("userId", [""]))[0]
    if not match or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", author):
        raise VideoError("invalid_input", "请使用带作品和作者编号的快手分享链接")
    return match[1], author


def parse_kuaishou(source, args):
    try:
        photo, author = kuaishou_parts(source)
    except VideoError:
        with public_get(source, PLATFORMS["kuaishou"], timeout=args.parse_timeout) as (_, final):
            source = final
        photo, author = kuaishou_parts(source)
    query = urlencode({"photoId": photo, "principalId": author})
    endpoint = "https://live.kuaishou.com/live_api/profile/feedbyid?" + query
    with public_get(endpoint, ("kuaishou.com",), referer="https://live.kuaishou.com/", timeout=args.parse_timeout) as (response, _):
        payload = bounded_json(response, 512 * 1024)
    work = (payload.get("data") or {}).get("currentWork") or {}
    if work.get("id") != photo or str(work.get("workType", "")).lower() != "video":
        raise VideoError("unsupported_content", "快手作品失效、受限或公开接口已变化")
    media_url = work.get("playUrl")
    https_url(media_url, KS_MEDIA)
    return {"platform": "kuaishou", "backend": "kuaishou", "id": photo, "sourceUrl": source,
            "title": "快手作品", "author": str((work.get("author") or {}).get("name", ""))[:120],
            "description": "", "durationSeconds": None, "watermark": None,
            "media": [{"url": media_url, "format": "mp4"}]}


def ytdlp_command(args):
    if args.yt_dlp:
        path = shutil.which(args.yt_dlp)
        if path:
            return [path]
        raise VideoError("missing_dependency", "指定的 yt-dlp 不存在")
    path = shutil.which("yt-dlp")
    if path:
        return [path]
    if importlib.util.find_spec("yt_dlp") is not None:
        return [sys.executable, "-m", "yt_dlp"]
    raise VideoError("missing_dependency", "请先安装 yt-dlp：python3 -m pip install yt-dlp")


def stop_process(proc):
    if os.name == "posix":
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    elif proc.poll() is None:
        proc.kill()
    proc.wait()


def directory_size(directory):
    total = 0
    for path in directory.iterdir():
        try:
            if path.is_file():
                total += path.stat().st_size
        except FileNotFoundError:
            pass  # yt-dlp may atomically rename a fragment between listing and stat.
    return total


def run_ytdlp(command, directory, timeout, disk_limit):
    deadline = time.monotonic() + timeout
    with (directory / "stdout.log").open("w+b") as stdout, (directory / "stderr.log").open("w+b") as stderr:
        proc = subprocess.Popen(command, stdout=stdout, stderr=stderr, stdin=subprocess.DEVNULL,
                                start_new_session=os.name == "posix")
        try:
            while proc.poll() is None:
                if time.monotonic() >= deadline:
                    raise VideoError("extractor_timeout", "yt-dlp 处理超时")
                if directory_size(directory) > disk_limit:
                    raise VideoError("too_large", "下载临时文件超过空间限额")
                if (directory / "stdout.log").stat().st_size > 4 * MIB or (directory / "stderr.log").stat().st_size > MIB:
                    raise VideoError("invalid_response", "yt-dlp 输出过大")
                time.sleep(0.1)
            if proc.returncode:
                # Never echo stderr: it can contain Cookie paths and signed URLs.
                raise VideoError("extractor_failed", "yt-dlp 解析或下载失败；检查链接、平台限制及本人 Cookie，必要时更新 yt-dlp")
            stdout.seek(0)
            raw = stdout.read(4 * MIB + 1)
            if len(raw) > 4 * MIB:
                raise VideoError("invalid_response", "yt-dlp 输出过大")
            return raw
        finally:
            if proc.poll() is None:
                stop_process(proc)


def ytdlp_options(args):
    opts = ["--ignore-config", "--no-plugin-dirs", "--no-cache-dir", "--no-playlist", "--no-warnings",
            "--socket-timeout", "12", "--retries", "1", "--fragment-retries", "1",
            "--use-extractors", "BiliBili,Douyin,XiaoHongShu", "-f", FORMAT.format(h=args.max_height)]
    if args.cookies:
        path = Path(args.cookies).expanduser().resolve()
        if not path.is_file():
            raise VideoError("cookie_file", "Cookie 文件不存在")
        opts += ["--cookies", str(path)]
    if args.ffmpeg_location:
        opts += ["--ffmpeg-location", args.ffmpeg_location]
    return opts


def parse_ytdlp(source, platform, args):
    host = https_url(source).hostname.lower()
    if matches(host, ("b23.tv", "xhslink.com")) or host == "v.douyin.com":
        with public_get(source, PLATFORMS[platform], timeout=min(20, args.parse_timeout)) as (_, final):
            source = final
    path = https_url(source).path
    supported_path = {
        "bilibili": r"/video/(?:BV[A-Za-z0-9]+|av\d+)/?",
        "douyin": r"/(?:video/|share/video/)\d{19}/?",
        "xiaohongshu": r"/(?:explore|discovery/item)/[A-Fa-f0-9]+/?",
    }
    if not re.fullmatch(supported_path[platform], path):
        raise VideoError("invalid_input", "请提供公开单视频作品链接，不支持账号、合集或付费节目页面")
    if platform == "douyin":
        source = "https://www.douyin.com/video/" + re.search(r"\d{19}", path)[0]
    public_addresses(https_url(source).hostname)
    command = ytdlp_command(args) + ytdlp_options(args) + ["--skip-download", "--dump-single-json", "--", source]
    with tempfile.TemporaryDirectory(prefix="video-parse-") as tmp:
        raw = run_ytdlp(command, Path(tmp), args.parse_timeout, 6 * MIB)
    try:
        meta = json.loads(raw)
    except (ValueError, UnicodeError):
        raise VideoError("invalid_response", "yt-dlp 没有返回有效元数据") from None
    if (not isinstance(meta, dict) or not meta.get("id") or not meta.get("title")
            or meta.get("is_live") or meta.get("live_status") in ("is_live", "is_upcoming", "post_live")
            or meta.get("_type") in ("playlist", "multi_video") or meta.get("entries") is not None):
        raise VideoError("unsupported_content", "仅接受已发布的公开单视频，不支持直播、图集或播放列表")
    if meta.get("availability") in ("private", "premium_only", "subscriber_only", "needs_auth"):
        raise VideoError("unsupported_content", "不支持私有、付费或订阅限定内容")
    duration = meta.get("duration")
    if duration is not None and (not isinstance(duration, (int, float)) or not math.isfinite(duration) or not 0 <= duration <= 21600):
        raise VideoError("unsupported_content", "视频时长无效或超过六小时")
    requested = meta.get("requested_formats") or [meta]
    media = []
    for item in requested:
        raw_url = item.get("url")
        if raw_url:
            try:
                https_url(raw_url)
            except VideoError:
                continue
            media.append({key: item.get(key) for key in ("url", "ext", "width", "height", "filesize", "vcodec", "acodec")})
    return {"platform": platform, "backend": "yt-dlp", "id": str(meta["id"]), "sourceUrl": source,
            "title": str(meta["title"])[:300], "author": str(meta.get("uploader") or "")[:120],
            "description": str(meta.get("description") or ""), "durationSeconds": duration,
            "watermark": None, "media": media}


def parse_video(source, platform, args):
    backend = args.backend
    if backend == "auto":
        backend = "kuaishou" if platform == "kuaishou" else (
            "dtk" if platform == "douyin" and os.environ.get("DTK_BASE_URL") else "yt-dlp")
    if backend == "dtk" and platform == "douyin":
        return parse_dtk(source, args)
    if backend == "kuaishou" and platform == "kuaishou":
        return parse_kuaishou(source, args)
    if backend == "yt-dlp" and platform != "kuaishou":
        return parse_ytdlp(source, platform, args)
    raise VideoError("backend_mismatch", "所选解析后端不适用于该平台")


def copy_response(response, path, maximum, deadline):
    declared = response.getheader("Content-Length")
    try:
        length = int(declared) if declared is not None else None
    except ValueError:
        raise VideoError("invalid_response", "来源文件长度无效") from None
    if length is not None and (length <= 0 or length > maximum):
        raise VideoError("too_large", "视频超过大小限额或为空")
    total = 0
    with path.open("xb") as out:
        os.chmod(path, 0o600)
        while True:
            if time.monotonic() >= deadline:
                raise VideoError("download_timeout", "下载超时")
            block = response.read(min(64 * 1024, maximum - total + 1))
            if not block:
                break
            total += len(block)
            if total > maximum:
                raise VideoError("too_large", "视频超过大小限额")
            out.write(block)
        out.flush()
        os.fsync(out.fileno())
    if length is not None and total != length:
        raise VideoError("incomplete_download", "下载字节数与来源声明不一致")
    return total


def verify_mp4(path, maximum, ffmpeg_location=None):
    size = path.stat().st_size
    if not 12 < size <= maximum:
        raise VideoError("too_large", "成品视频为空或超过大小限额")
    with path.open("rb") as f:
        if f.read(12)[4:8] != b"ftyp":
            raise VideoError("invalid_media", "下载内容不是 MP4 文件")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg_location:
        location = Path(ffmpeg_location)
        candidate = (location if location.is_dir() else location.parent) / ("ffprobe.exe" if os.name == "nt" else "ffprobe")
        ffprobe = str(candidate) if candidate.is_file() else ffprobe
    if ffprobe:
        check = subprocess.run([ffprobe, "-v", "error", "-show_entries", "stream=codec_type", "-of", "json", str(path)],
                               capture_output=True, timeout=20)
        try:
            streams = json.loads(check.stdout).get("streams", [])
        except ValueError:
            streams = []
        if check.returncode or not any(s.get("codec_type") == "video" for s in streams):
            raise VideoError("invalid_media", "ffprobe 未检测到有效视频轨道")
    sha = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(MIB), b""):
            sha.update(block)
    return size, sha.hexdigest(), bool(ffprobe)


def download_video(result, args):
    root = Path(args.output_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if shutil.disk_usage(root).free < args.max_bytes * 3 + 16 * MIB:
        raise VideoError("disk_space", "输出磁盘没有足够的下载和合并空间")
    with tempfile.TemporaryDirectory(prefix=".video-", dir=root) as tmp:
        directory = Path(tmp)
        target = directory / "video.mp4"
        if result["backend"] == "yt-dlp":
            command = ytdlp_command(args) + ytdlp_options(args) + ["--no-progress", "--max-filesize", str(args.max_bytes),
                "--merge-output-format", "mp4", "--remux-video", "mp4", "--no-mtime", "-o", str(directory / "video.%(ext)s"),
                "--", result["sourceUrl"]]
            run_ytdlp(command, directory, args.download_timeout, args.max_bytes * 3 + 8 * MIB)
        else:
            if not result["media"]:
                raise VideoError("no_media", "没有满足大小、清晰度和无水印条件的 MP4")
            media = result["media"][0]
            suffixes = DY_MEDIA if result["backend"] == "dtk" else KS_MEDIA
            deadline = time.monotonic() + args.download_timeout
            with public_get(media["url"], suffixes, referer=result["sourceUrl"], timeout=min(20, args.download_timeout)) as (response, _):
                total = copy_response(response, target, args.max_bytes, deadline)
            if media.get("size_bytes") is not None and total != media["size_bytes"]:
                raise VideoError("incomplete_download", "实际大小与解析元数据不一致")
        if not target.is_file():
            raise VideoError("no_file", "来源未生成 MP4 文件，可能超限或格式不可用")
        size, digest, probed = verify_mp4(target, args.max_bytes, args.ffmpeg_location)
        safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", result["id"])[:80]
        final = root / f"{result['platform']}-{safe_id}-{uuid.uuid4().hex[:12]}.mp4"
        os.chmod(target, 0o600)
        os.link(target, final)  # Same filesystem, atomic, and never overwrites existing files.
    return {**{k: v for k, v in result.items() if k != "media"}, "file": str(final), "sizeBytes": size,
            "sha256": digest, "videoTrackVerified": probed}


def parser():
    p = argparse.ArgumentParser(description="解析/下载抖音、哔哩哔哩、小红书、快手公开单视频")
    p.add_argument("action", choices=("doctor", "parse", "download"))
    p.add_argument("input", nargs="?", help="唯一分享文本或链接")
    p.add_argument("--input-file", help="UTF-8 分享文本文件；- 表示 stdin")
    p.add_argument("--output-dir", default="./downloads")
    p.add_argument("--backend", choices=("auto", "yt-dlp", "dtk", "kuaishou"), default="auto")
    p.add_argument("--yt-dlp", help="独立安装的 yt-dlp 可执行文件")
    p.add_argument("--ffmpeg-location", help="FFmpeg 可执行文件或所在目录")
    p.add_argument("--cookies", help="本人授权的 Netscape Cookie 文件，不自动读取浏览器")
    p.add_argument("--max-mib", type=int, default=95)
    p.add_argument("--max-height", type=int, default=1080)
    p.add_argument("--parse-timeout", type=int, default=45)
    p.add_argument("--download-timeout", type=int, default=180)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if not 1 <= args.max_mib <= 4096 or not 1 <= args.max_height <= 4320 or not 1 <= args.parse_timeout <= 600 or not 1 <= args.download_timeout <= 3600:
            raise VideoError("invalid_option", "大小、清晰度或超时参数超出范围")
        args.max_bytes = args.max_mib * MIB
        if args.action == "doctor":
            try:
                command = ytdlp_command(args)
            except VideoError:
                command = None
            output = {"python": sys.version.split()[0], "ytDlpAvailable": command is not None,
                      "ffmpegAvailable": bool(args.ffmpeg_location or shutil.which("ffmpeg")),
                      "ffprobeAvailable": bool(shutil.which("ffprobe")),
                      "dtkConfigured": bool(os.environ.get("DTK_BASE_URL")), "platforms": list(PLATFORMS)}
        else:
            if bool(args.input) == bool(args.input_file):
                raise VideoError("invalid_input", "请提供分享文本或 --input-file，两者选一")
            if args.input_file:
                if args.input_file == "-":
                    text = sys.stdin.read(2049)
                else:
                    with open(args.input_file, encoding="utf-8") as f:
                        text = f.read(2049)
            else:
                text = args.input
            source, platform = extract(text)
            result = parse_video(source, platform, args)
            output = download_video(result, args) if args.action == "download" else result
        print(json.dumps(output, ensure_ascii=False, allow_nan=False))
        return 0
    except VideoError as exc:
        code, message = exc.code, str(exc)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, http.client.HTTPException, subprocess.SubprocessError):
        code, message = "runtime_error", "处理失败：检查网络、来源响应、文件权限和运行依赖；没有发布不完整文件"
    except KeyboardInterrupt:
        code, message = "cancelled", "操作已取消"
    print(json.dumps({"error": {"code": code, "message": message}}, ensure_ascii=False), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
