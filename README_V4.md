# B站合集视频下载器 V4

## 🆕 V4版本新特性

- ✅ **Cookie认证支持**: 支持SESSDATA等Cookie，下载会员专享内容
- ✅ **真实名称**: 合集文件夹使用真实名称，视频文件使用真实标题
- ✅ **智能格式回退**: 自动尝试多种格式，解决下载失败问题
- ✅ **灵活Cookie配置**: 支持全局Cookie和合集专用Cookie
- ✅ **安全Cookie处理**: 自动创建和清理临时Cookie文件

## 🚀 快速开始

### 基本使用（无Cookie）

```bash
# 安装依赖
pip install -r requirements.txt

# 下载合集
python bilibili_collection_downloader_v4.py \
  "https://space.bilibili.com/351754674/lists/2416048?type=season" \
  "/Volumes/nas-mk/" \
  --max-videos 10
```

### 使用Cookie（推荐）

```bash
# 使用您提供的SESSDATA
python bilibili_collection_downloader_v4.py \
  "https://space.bilibili.com/351754674/lists/2416048?type=season" \
  "/Volumes/nas-mk/" \
  --cookies "SESSDATA=66b7bf77%2C1770468788%2Cb735f%2A81CjDSbHHzy6WMttmU2eHPr0MTeOve6vrO86MCLjDoUxEGIuhGkJ6nacYAldqhqKT1yxISVlpVMkVRblk5QXVJV0lrQy1jQW45bXh3a3lSRk5LU1FlU1dIcHZrMW1LNlo4azRRS2hCeWFCMGcydlVTR2phWTcyU29mTkZSakZvOF9YUEZRUVNfMnRRIIEC" \
  --max-videos 10
```

## 📁 预期文件结构

```
/Volumes/nas-mk/
└── 合集·乔布斯合集/                    # 自动获取的真实合集名
    ├── download.log                   # 下载日志
    ├── video_list.json               # 视频列表
    ├── 经典时刻：乔布斯发布初代iPhone.mp4    # 真实视频标题
    ├── 经典时刻：乔布斯发布初代iPhone.nfo    # Emby元数据
    ├── 经典时刻：乔布斯发布初代iPhone.jpg    # 视频缩略图
    ├── 经典时刻：乔布斯发布初代iPhone.info.json
    └── ...
```

## 🍪 Cookie使用

### 获取SESSDATA

1. 登录B站
2. 按F12打开开发者工具
3. 切换到"Application" → "Cookies" → "https://www.bilibili.com"
4. 找到"SESSDATA"并复制其值

详细说明请查看 `COOKIE_GUIDE.md`

### Cookie使用方式

```bash
# 方法1：直接指定SESSDATA
--cookies "SESSDATA=你的SESSDATA值"

# 方法2：多个Cookie
--cookies "SESSDATA=xxx; buvid3=yyy; bili_jct=zzz"

# 方法3：Cookie文件
--cookies-file cookies.txt
```

## ⚙️ 批量下载

```bash
# 编辑配置文件
cp collections_config_v4.json my_collections.json

# 批量下载（使用全局Cookie）
python batch_download_v4.py \
  my_collections.json \
  ./downloads \
  --cookies "SESSDATA=你的SESSDATA"
```

## 🎯 主要参数

| 参数 | 说明 | 示例 |
|------|------|------|
| `--cookies` | Cookie字符串或文件路径 | `"SESSDATA=xxx"` |
| `--name` | 自定义合集名称 | `"我的合集"` |
| `--max-videos` | 最大下载数量 | `10` |
| `--quality` | 视频质量 | `"best[height<=720]"` |
| `--verbose` | 详细输出 | - |

## 🔧 格式回退机制

V4版本会自动尝试以下格式（按优先级）：

1. `best[height<=1080]` - 1080p及以下最佳质量
2. `best[height<=720]` - 720p及以下最佳质量  
3. `best[height<=480]` - 480p及以下最佳质量
4. `best[ext=mp4]` - MP4格式最佳质量
5. `best[ext=flv]` - FLV格式最佳质量
6. `best` - 最佳可用质量
7. `worst` - 最低质量（通常可用）

## 🛡️ 安全提醒

- Cookie包含您的登录信息，请妥善保管
- 不要将Cookie分享给他人
- 使用完毕后会自动清理临时Cookie文件
- 建议使用专门的下载账号

## 📊 版本对比

| 功能 | V3 | V4 |
|------|----|----|
| 真实名称 | ✅ | ✅ |
| 格式回退 | ✅ | ✅ |
| Cookie支持 | ❌ | ✅ |
| 会员内容 | ❌ | ✅ |
| 高清画质 | 部分 | ✅ |

## 🎯 使用场景

### 场景1：公开内容下载
```bash
python bilibili_collection_downloader_v4.py "合集URL" "./downloads"
```

### 场景2：会员内容下载
```bash
python bilibili_collection_downloader_v4.py \
  "合集URL" \
  "./downloads" \
  --cookies "SESSDATA=你的SESSDATA"
```

### 场景3：批量下载不同权限合集
编辑配置文件，为不同合集设置不同Cookie：

```json
[
  {
    "name": "公开合集",
    "url": "公开合集URL",
    "use_auto_name": true
  },
  {
    "name": "会员合集", 
    "url": "会员合集URL",
    "use_auto_name": true,
    "cookies": "SESSDATA=专用Cookie"
  }
]
```

## 📞 故障排除

### 下载失败
1. 检查网络连接
2. 验证Cookie是否有效
3. 尝试更新yt-dlp: `pip install --upgrade yt-dlp`

### Cookie相关问题
1. 确认Cookie格式正确
2. 检查Cookie是否过期
3. 重新获取SESSDATA

### 格式不可用
V4版本会自动处理，如果所有格式都失败：
1. 检查视频是否存在
2. 确认是否需要特殊权限
3. 尝试使用Cookie

## 📋 文件说明

- `bilibili_collection_downloader_v4.py` - 主下载器
- `batch_download_v4.py` - 批量下载器  
- `collections_config_v4.json` - 配置文件示例
- `COOKIE_GUIDE.md` - Cookie详细使用指南
- `requirements.txt` - 依赖文件

立即开始使用V4版本，享受Cookie认证带来的更好下载体验！

