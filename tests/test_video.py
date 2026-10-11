# SPDX-License-Identifier: MIT
import argparse
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("video", Path(__file__).resolve().parents[1] / "scripts/video.py")
video = importlib.util.module_from_spec(spec)
spec.loader.exec_module(video)

MP4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 52
DY = "https://www.douyin.com/video/7689314954993847587"
KS = "https://v.m.chenzhongtech.com/fw/photo/abc?photoId=abc&userId=user_2"


def options(**overrides):
    values = dict(backend="auto", max_bytes=95 * video.MIB, max_height=1080, parse_timeout=45,
                  download_timeout=180, yt_dlp=None, cookies=None, ffmpeg_location=None)
    values.update(overrides)
    return argparse.Namespace(**values)


def dtk_content():
    return {"platform": "douyin", "content_id": "7689314954993847587", "kind": "video",
            "title": "第一行", "description": "第一行\n完整文案 😀", "author": {"nickname": "作者"},
            "duration_ms": 1200, "media": {"streams": [
                {"url": "https://video.zjcdn.com/first.mp4", "format": "mp4", "watermark": False,
                 "width": 1280, "height": 720, "size_bytes": 1000},
                {"url": "https://video.zjcdn.com/small.mp4", "format": "mp4", "watermark": False,
                 "width": 640, "height": 360, "size_bytes": len(MP4)},
            ]}}


class Response(io.BytesIO):
    def __init__(self, body, length=None):
        super().__init__(body)
        self.length = length

    def getheader(self, name):
        return self.length if name == "Content-Length" else None


class Inputs(unittest.TestCase):
    def test_four_platforms_and_share_text(self):
        for text, platform in [(DY, "douyin"), ("分享 https://v.douyin.com/abc123/ 复制打开", "douyin"),
                               ("https://b23.tv/abc", "bilibili"), ("https://www.xiaohongshu.com/explore/abc?xsec_token=example", "xiaohongshu"),
                               ("https://v.kuaishou.com/abc", "kuaishou"), (KS, "kuaishou")]:
            with self.subTest(text=text):
                self.assertEqual(video.extract(text)[1], platform)

    def test_untrusted_inputs_rejected(self):
        for text in ["", "https://b23.tv/abc https://b23.tv/def", "http://b23.tv/abc", "https://b23.tv:444/abc",
                     "https://b23.tv.evil.example/abc", "https://b23.tv@127.0.0.1/a", "https://127.0.0.1/video/a",
                     "https://weixin.qq.com/sph/abc", "https://www.douyin.com/user/123", "你" * 2049,
                     "https://v.douyin.com/a\\evil", "https://v.m.chenzhongtech.com/fw/photo/abc?photoId=def&userId=user"]:
            with self.subTest(text=text[:80]), self.assertRaises(video.VideoError):
                video.extract(text)

    def test_private_dns_and_mixed_answers_rejected(self):
        for ips in [("127.0.0.1",), ("100.64.0.1",), ("2001:db8::1",), ("8.8.8.8", "10.0.0.1")]:
            with patch.object(video.socket, "getaddrinfo", return_value=[(0, 0, 0, "", (ip, 443)) for ip in ips]):
                with self.assertRaises(video.VideoError):
                    video.public_addresses("v.douyin.com")

    def test_public_dns_accepted(self):
        with patch.object(video.socket, "getaddrinfo", return_value=[(0, 0, 0, "", ("8.8.8.8", 443))]):
            self.assertEqual(video.public_addresses("example.com"), ["8.8.8.8"])

    def test_cdn_suffix_boundary(self):
        video.https_url("https://media.a.kwimgs.com/a.mp4", video.KS_MEDIA)
        for raw in ["https://kwimgs.com.evil.example/a.mp4", "http://a.kwimgs.com/a.mp4", "https://a.kwimgs.com@127.0.0.1/a.mp4"]:
            with self.assertRaises(video.VideoError):
                video.https_url(raw, video.KS_MEDIA)

    def test_cross_domain_redirect_rejected_before_second_connection(self):
        response = unittest.mock.Mock(status=302)
        response.getheader.return_value = "https://evil.example/video.mp4"
        connection = unittest.mock.Mock()
        connection.getresponse.return_value = response
        with patch.object(video, "PublicHTTPS", return_value=connection) as create:
            with self.assertRaises(video.VideoError):
                with video.public_get("https://media.kwimgs.com/a.mp4", video.KS_MEDIA):
                    self.fail("untrusted redirect reached")
        self.assertEqual(create.call_count, 1)
        connection.close.assert_called_once()


class DTK(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"DTK_BASE_URL": "http://127.0.0.1:18071", "DTK_API_KEY": "test-only-not-a-credential"}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_complete_description_and_smallest_verified_media(self):
        with patch.object(video, "dtk_call", return_value=(dtk_content(), 200)) as call:
            result = video.parse_dtk(DY, options())
        self.assertEqual(result["description"], "第一行\n完整文案 😀")
        self.assertTrue(result["media"][0]["url"].endswith("small.mp4"))
        self.assertIs(result["watermark"], False)
        self.assertEqual(call.call_args.args[3], {"url": DY, "include_raw": False})

    def test_queued_result_waits_for_nested_content(self):
        calls = [({"task_id": "00000000-0000-0000-0000-000000000001"}, 202),
                 ({"state": "running"}, 200), ({"state": "done", "data": dtk_content()}, 200)]
        with patch.object(video, "dtk_call", side_effect=calls) as call, patch.object(video.time, "sleep"):
            result = video.parse_dtk(DY, options())
        self.assertEqual(call.call_count, 3)
        self.assertEqual(result["id"], "7689314954993847587")

    def test_http_success_with_failed_task_is_failure(self):
        calls = [({"task_id": "00000000-0000-0000-0000-000000000001"}, 202),
                 ({"state": "failed", "error": {"message": "private detail"}}, 200)]
        with patch.object(video, "dtk_call", side_effect=calls), patch.object(video.time, "sleep"):
            with self.assertRaises(video.VideoError) as error:
                video.parse_dtk(DY, options())
        self.assertEqual(error.exception.code, "dtk_task_failed")

    def test_private_deleted_album_or_wrong_id_rejected(self):
        for changes in [{"is_private": True}, {"is_deleted": True}, {"kind": "album"}, {"content_id": "7689314954993847588"}]:
            data = {**dtk_content(), **changes}
            with patch.object(video, "dtk_call", return_value=(data, 200)), self.assertRaises(video.VideoError):
                video.parse_dtk(DY, options())

    def test_bad_streams_never_claim_no_watermark(self):
        for changes in [{"watermark": True}, {"watermark": None}, {"size_bytes": 999999999},
                        {"url": "https://evil.example/a.mp4"}, {"height": 2160}]:
            data = dtk_content()
            data["media"]["streams"] = [{**data["media"]["streams"][0], **changes}]
            with patch.object(video, "dtk_call", return_value=(data, 200)):
                result = video.parse_dtk(DY, options())
            self.assertEqual(result["media"], [])
            self.assertIsNone(result["watermark"])

    def test_remote_plaintext_and_embedded_keys_rejected(self):
        for base in ["http://example.com", "https://user:key@example.com", "https://example.com?key=example"]:
            with self.assertRaises(video.VideoError):
                video.dtk_base(base)
        video.dtk_base("https://example.com")
        video.dtk_base("http://[::1]:18071")


class Kuaishou(unittest.TestCase):
    def test_matching_author_and_photo(self):
        self.assertEqual(video.kuaishou_parts(KS), ("abc", "user_2"))

    def test_response_identity_and_media_validation(self):
        payload = {"data": {"currentWork": {"id": "abc", "workType": "video", "playUrl": "https://media.kwimgs.com/a.mp4", "author": {"name": "作者"}}}}

        @contextmanager
        def request(*args, **kwargs):
            yield Response(json.dumps(payload).encode()), args[0]

        with patch.object(video, "public_get", request):
            result = video.parse_kuaishou(KS, options())
            self.assertEqual(result["author"], "作者")
            self.assertIsNone(result["watermark"])
            payload["data"]["currentWork"]["id"] = "other"
            with self.assertRaises(video.VideoError):
                video.parse_kuaishou(KS, options())


class Files(unittest.TestCase):
    def test_declared_length_mismatch_and_unknown_length_overflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            for body, length, cap in [(b"small", "1024", 2048), (b"x" * 20, None, 10), (b"x", "-1", 100)]:
                target = Path(tmp) / os.urandom(4).hex()
                with self.assertRaises(video.VideoError):
                    video.copy_response(Response(body, length), target, cap, float("inf"))

    def test_invalid_mp4_is_not_validated(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "video.mp4"
            p.write_bytes(b"<html>not a video</html>")
            with self.assertRaises(video.VideoError):
                video.verify_mp4(p, 1000)

    def test_atomic_publish_and_checksum(self):
        @contextmanager
        def request(*args, **kwargs):
            yield Response(MP4, str(len(MP4))), args[0]

        result = {"platform": "kuaishou", "backend": "kuaishou", "id": "../../name", "sourceUrl": KS,
                  "watermark": None, "media": [{"url": "https://media.kwimgs.com/a.mp4"}]}
        with tempfile.TemporaryDirectory() as tmp:
            args = options(output_dir=tmp)
            with patch.object(video, "public_get", request), patch.object(video.shutil, "which", return_value=None):
                first = video.download_video(result, args)
                second = video.download_video(result, args)
            self.assertNotEqual(first["file"], second["file"])
            self.assertEqual(Path(first["file"]).parent, Path(tmp))
            self.assertEqual(Path(first["file"]).read_bytes(), MP4)
            self.assertEqual(first["sha256"], hashlib.sha256(MP4).hexdigest())
            self.assertIs(first["videoTrackVerified"], False)
            self.assertNotIn("media", first)
            self.assertFalse(any(p.name.startswith(".video-") for p in Path(tmp).iterdir()))

    def test_failed_download_leaves_existing_files_and_no_partial(self):
        @contextmanager
        def request(*args, **kwargs):
            yield Response(MP4[:10], "1024"), args[0]

        result = {"platform": "kuaishou", "backend": "kuaishou", "id": "abc", "sourceUrl": KS,
                  "media": [{"url": "https://media.kwimgs.com/a.mp4"}]}
        with tempfile.TemporaryDirectory() as tmp:
            existing = Path(tmp) / "existing.mp4"
            existing.write_bytes(b"keep")
            with patch.object(video, "public_get", request), self.assertRaises(video.VideoError):
                video.download_video(result, options(output_dir=tmp))
            self.assertEqual(existing.read_bytes(), b"keep")
            self.assertEqual(list(Path(tmp).iterdir()), [existing])

    def test_metadata_size_mismatch_fails_before_publish(self):
        @contextmanager
        def request(*args, **kwargs):
            yield Response(MP4, str(len(MP4))), args[0]

        result = {"platform": "douyin", "backend": "dtk", "id": "abc", "sourceUrl": DY,
                  "media": [{"url": "https://video.zjcdn.com/a.mp4", "size_bytes": len(MP4) + 1}]}
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(video, "public_get", request), self.assertRaises(video.VideoError):
                video.download_video(result, options(output_dir=tmp))
            self.assertEqual(list(Path(tmp).iterdir()), [])


class Extractor(unittest.TestCase):
    def test_metadata_parse_only_and_no_unknown_watermark_claim(self):
        meta = {"id": "BVexample", "title": "公开视频", "description": "正文", "duration": 10,
                "url": "https://cdn.example/video.mp4", "ext": "mp4"}
        with patch.object(video, "public_addresses"), patch.object(video, "ytdlp_command", return_value=["yt-dlp"]), \
                patch.object(video, "run_ytdlp", return_value=json.dumps(meta).encode()) as run:
            result = video.parse_ytdlp("https://www.bilibili.com/video/BVexample", "bilibili", options())
        self.assertIn("--skip-download", run.call_args.args[0])
        self.assertIsNone(result["watermark"])
        self.assertEqual(result["description"], "正文")

    def test_live_playlist_and_premium_rejected(self):
        for changes in [{"is_live": True}, {"_type": "playlist", "entries": []}, {"availability": "premium_only"}, {"duration": float("nan")}]:
            meta = {"id": "example", "title": "公开视频", **changes}
            with patch.object(video, "public_addresses"), patch.object(video, "ytdlp_command", return_value=["yt-dlp"]), \
                    patch.object(video, "run_ytdlp", return_value=json.dumps(meta).encode()), self.assertRaises(video.VideoError):
                video.parse_ytdlp("https://www.bilibili.com/video/BVexample", "bilibili", options())

    def test_short_link_resolves_within_platform_before_extraction(self):
        final = "https://www.xiaohongshu.com/explore/6411cf99000000001300b6d9?xsec_token=example"
        meta = {"id": "6411cf99000000001300b6d9", "title": "公开视频"}

        @contextmanager
        def request(*args, **kwargs):
            self.assertEqual(args[1], video.PLATFORMS["xiaohongshu"])
            yield Response(b""), final

        with patch.object(video, "public_get", request), patch.object(video, "public_addresses"), \
                patch.object(video, "ytdlp_command", return_value=["yt-dlp"]), \
                patch.object(video, "run_ytdlp", return_value=json.dumps(meta).encode()) as run:
            result = video.parse_ytdlp("https://xhslink.com/a/abc", "xiaohongshu", options())
        self.assertEqual(result["sourceUrl"], final)
        self.assertEqual(run.call_args.args[0][-1], final)

    def test_subprocess_failure_does_not_expose_sensitive_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(video.VideoError) as error:
                video.run_ytdlp([os.sys.executable, "-c", "import sys; print('private-cookie-data',file=sys.stderr); sys.exit(1)"],
                                 Path(tmp), 2, video.MIB)
            self.assertNotIn("private-cookie-data", str(error.exception))

    def test_subprocess_timeout_terminates_promptly(self):
        started = time.monotonic()
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(video.VideoError) as error:
                video.run_ytdlp([os.sys.executable, "-c", "import time; time.sleep(10)"], Path(tmp), 0.01, video.MIB)
        self.assertEqual(error.exception.code, "extractor_timeout")
        self.assertLess(time.monotonic() - started, 3)

    def test_cli_input_file_routes_and_returns_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "share.txt"
            source.write_text("分享 " + DY + " 复制打开", encoding="utf-8")
            result = {"platform": "douyin", "backend": "dtk", "id": "example", "sourceUrl": DY, "media": []}
            output = {"file": str(Path(tmp) / "result.mp4"), "sha256": "example"}
            stdout = io.StringIO()
            with patch.object(video, "parse_video", return_value=result) as parse, \
                    patch.object(video, "download_video", return_value=output), redirect_stdout(stdout):
                self.assertEqual(video.main(["download", "--input-file", str(source)]), 0)
            self.assertEqual(parse.call_args.args[:2], (DY, "douyin"))
            self.assertEqual(json.loads(stdout.getvalue()), output)

    @unittest.skipUnless(os.name == "posix", "fake executable uses POSIX shebang")
    def test_full_ytdlp_download_pipeline_with_local_fake_extractor(self):
        original_which = video.shutil.which
        with tempfile.TemporaryDirectory() as tmp:
            executable = Path(tmp) / "fake-yt-dlp"
            executable.write_text(
                "#!" + os.sys.executable + "\nimport json,sys\nfrom pathlib import Path\n"
                "if '--skip-download' in sys.argv:\n"
                " print(json.dumps({'id':'BVexample','title':'公开测试视频','duration':1}))\n"
                "else:\n"
                " dest=sys.argv[sys.argv.index('-o')+1].replace('%(ext)s','mp4')\n"
                " Path(dest).write_bytes(" + repr(MP4) + ")\n", encoding="utf-8")
            executable.chmod(0o700)
            stdout = io.StringIO()
            with patch.object(video, "public_addresses"), \
                    patch.object(video.shutil, "which", side_effect=lambda name: None if name == "ffprobe" else original_which(name)), \
                    redirect_stdout(stdout):
                status = video.main(["download", "https://www.bilibili.com/video/BVexample", "--yt-dlp", str(executable), "--output-dir", tmp])
            self.assertEqual(status, 0)
            result = json.loads(stdout.getvalue())
            self.assertEqual(Path(result["file"]).read_bytes(), MP4)
            self.assertEqual(result["sha256"], hashlib.sha256(MP4).hexdigest())

    def test_cli_failure_json_has_nonzero_exit(self):
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            self.assertEqual(video.main(["download", "https://evil.example/a"]), 1)
        self.assertEqual(json.loads(stderr.getvalue())["error"]["code"], "unsupported_platform")


if __name__ == "__main__":
    unittest.main()
