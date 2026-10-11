# 抖音 DTK 接入

适用于安装者已经自行部署并授权使用 [DTK v5](https://github.com/Evil0ctal/Douyin_TikTok_Download_API) 的情况。部署和创建最小 `douyin:read` API key 以 [官方文档](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/documents/en/11-api.md) 为准；本 Skill 不管理该服务。

只配置服务根 URL，不附加 `/api/v1`，例如 `http://127.0.0.1:18071` 或安装者自己的 HTTPS 地址。HTTP 仅限 `localhost`、`127.0.0.1` 和 `::1`。服务重定向不会被跟随，避免转发 API key。

```bash
export DTK_BASE_URL='http://127.0.0.1:18071'
export DTK_API_KEY_FILE='/private/dtk-api-key.txt'
python3 scripts/video.py parse --input-file /path/to/share.txt
python3 scripts/video.py download --input-file /path/to/share.txt --output-dir ./downloads
```

`DTK_API_KEY_FILE` 为只包含密钥的 UTF-8 文件，建议权限 `600`；也支持由运行环境注入 `DTK_API_KEY`。文件配置优先。示例不包含默认密钥，不提交 `.env`、Cookie 或运行结果。

接口调用：

- `POST /api/v1/parse?wait=20`，JSON 为 `{"url":"公开分享链接","include_raw":false}`，请求头 `X-API-Key`。
- HTTP 200：成功结果是 envelope 的 `data`。
- HTTP 202：`data.task_id` 只是任务编号。继续有界轮询 `GET /api/v1/tasks/{task_id}`；`state=done` 的内容位于 **envelope.data.data**。
- 读取任务成功可返回 HTTP 200 / `success=true`，但 `data.state=failed` 仍然是解析失败，不能当成功。

只接受 `platform=douyin`、`kind=video`、未删除且非私有的作品。保留完整 `description`；成品候选须明确 `watermark=false`、`format=mp4`，大小与尺寸已知且符合限额。沿用项目优先选择最小合格 MP4 的策略，便于移动端传输。没有符合条件的候选仍可解析文案，但下载返回 `no_media`。

媒体连接限制为项目现有抖音 CDN 域，校验公网 DNS 并绑定已校验 IP，HTTPS 仍验证原域名。更换平台 CDN 时应先验证来源，再更新允许清单和测试。下载数据不会回传到域动工坊。
