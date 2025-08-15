# Legacy CLI（V4/V5）使用说明

更新时间：2025-08-15 18:12 (Asia/Shanghai)

V6 以服务端与 Web 管理为主，以下命令行脚本为历史兼容，建议仅在特殊场景使用。

## 目录结构
- legacy/v5/bilibili_collection_downloader_v5.py
- legacy/collections_config_v4.json

## 基本使用（V5）
```bash
python legacy/v5/bilibili_collection_downloader_v5.py \
  "https://space.bilibili.com/351754674/lists/2416048?type=season" \
  "/Volumes/nas-mk/" \
  --cookies "SESSDATA=your_sessdata_here"
```

## 批量下载（V4 Config）
```bash
python legacy/batch_download_v4.py --config legacy/collections_config_v4.json
```

## 注意
- 该模式不包含 V6 的队列/统计/自动导入等能力。
- 若与 V6 同时使用，请确保输出目录与 V6 下载目录策略一致，避免跨合集误判。
