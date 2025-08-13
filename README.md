# bili_curator - B站合集智能下载器

> 🚀 专业的B站合集视频下载和管理工具，支持智能增量下载、Emby媒体库集成

## ✨ 核心特性

### 🧠 智能增量下载
- **基于实际文件检测**：扫描目录中的JSON文件，从中读取真实视频ID
- **完整性自动验证**：检查文件大小和完整性，自动修复损坏下载
- **零配置增量**：无需维护下载列表，基于现有文件智能跳过

### 📁 智能文件管理
- **视频标题命名**：默认使用清晰的视频标题作为文件名
- **ID存储在JSON**：视频ID安全存储在info.json文件中
- **统一命名规则**：自动处理特殊字符，确保系统兼容性

### 🎬 完美媒体库支持
- **增强NFO文件**：丰富的元数据，完美支持Emby/Jellyfin
- **标准文件结构**：符合媒体服务器最佳实践
- **自动缩略图**：下载视频封面图片

### ⚡ 高效下载策略
- **多线程并发**：支持多个视频同时下载
- **智能格式回退**：自动尝试最佳视频格式
- **Cookie认证**：支持会员专享内容下载

## 🚀 快速开始

### 环境准备
```bash
# 激活Python虚拟环境
source ~/.pydev/bin/activate

# 安装依赖
pip install -r requirements.txt
```

### 基本使用
```bash
# 下载合集（推荐用法）
python bilibili_collection_downloader_v5.py \
  "https://space.bilibili.com/351754674/lists/2416048?type=season" \
  "/Volumes/nas-mk/" \
  --cookies "SESSDATA=your_sessdata_here"
```

### 高级配置
```bash
# 完整配置示例
python bilibili_collection_downloader_v5.py \
  "collection_url" "/output/dir" \
  --cookies "SESSDATA=your_sessdata" \
  --naming title \
  --max-videos 50 \
  --max-workers 3 \
  --collection-name "自定义合集名"
```

## 📁 文件结构

下载完成后的文件结构：
```
/output/合集·乔布斯合集/
├── 经典时刻：乔布斯发布初代iPhone.mp4          # 视频文件
├── 经典时刻：乔布斯发布初代iPhone.info.json    # 元数据（包含视频ID）
├── 经典时刻：乔布斯发布初代iPhone.nfo          # Emby元数据
├── 经典时刻：乔布斯发布初代iPhone.jpg          # 缩略图
├── 乔布斯2005斯坦福演讲.mp4                    # 另一个视频
├── 乔布斯2005斯坦福演讲.info.json              # 对应元数据
├── video_details.json                          # 合集视频列表
└── download_v5.log                             # 下载日志
```

## 🔄 智能增量下载原理

### 工作流程
1. **扫描现有文件**：读取目录中所有`.info.json`文件
2. **提取视频ID**：从JSON文件中获取真实的视频ID
3. **完整性检查**：验证对应的mp4文件是否存在且完整
4. **智能跳过**：已完整下载的视频自动跳过
5. **增量下载**：只下载缺失或损坏的视频

### 支持的场景
- ✅ **首次下载**：下载合集中的所有视频
- ✅ **增量更新**：合集新增视频时，只下载新视频
- ✅ **修复下载**：自动检测并重新下载损坏的文件
- ✅ **任意文件名**：支持重命名后的文件（基于JSON内容识别）

## 🛠️ 工具脚本

### 批量下载
```bash
# 使用配置文件批量下载多个合集
python batch_download_v4.py --config collections_config_v4.json
```

### 诊断工具
```bash
# 检查Cookie和网络连接
python diagnose_bilibili.py --test-cookie
```

### 清理废弃脚本
```bash
# 清理不再需要的旧版本脚本
python cleanup_obsolete_scripts.py --execute
```

## ⚙️ 配置选项

### 命令行参数
| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--naming` | `title` | 文件命名策略：`title`(标题) 或 `id`(视频ID) |
| `--max-videos` | 无限制 | 最大下载视频数量 |
| `--max-workers` | `3` | 并发下载线程数 |
| `--quality` | `best[height<=1080]` | 视频质量选择 |
| `--cookies` | 无 | Cookie字符串（支持会员内容） |
| `--collection-name` | 自动获取 | 自定义合集目录名 |

### Cookie配置
```bash
# 从浏览器获取SESSDATA
--cookies "SESSDATA=your_sessdata_value"

# 完整Cookie字符串
--cookies "SESSDATA=xxx; bili_jct=yyy; DedeUserID=zzz"
```

## 📝 NFO文件增强

V5版本生成的NFO文件包含丰富元数据：
- ✅ 详细视频描述
- ✅ 上传者信息
- ✅ 上传日期和时长
- ✅ 观看数和点赞数
- ✅ 视频标签
- ✅ 流媒体信息
- ✅ 唯一标识符

## 🔧 故障排除

### 常见问题

**Q: 下载失败怎么办？**
A: 重新运行相同命令，系统会自动检测并重新下载失败的文件。

**Q: 如何更新已下载的合集？**
A: 直接运行相同的下载命令，系统会自动下载新增的视频。

**Q: Cookie过期怎么办？**
A: 从浏览器重新获取SESSDATA，更新命令中的`--cookies`参数。

**Q: 文件命名有问题？**
A: 使用`--naming id`切换到ID命名模式，或手动重命名后重新运行。

### 日志查看
```bash
# 查看详细下载日志
tail -f /output/dir/download_v5.log
```

## 🆚 版本对比

| 特性 | V4版本 | V5版本 |
|------|--------|--------|
| 增量下载 | 基于JSON配置文件 | 基于实际文件扫描 |
| 文件命名 | 不一致 | 统一规范 |
| NFO质量 | 基础信息 | 丰富元数据 |
| 并发支持 | 无 | 多线程 |
| 错误处理 | 基础 | 智能重试 |
| 完整性检查 | 无 | 自动验证 |

## 📚 相关文档

- [智能增量下载详解](SMART_INCREMENTAL_EXAMPLE.md)
- [V5版本改进指南](IMPROVEMENT_GUIDE_V5.md)
- [Cookie使用指南](COOKIE_GUIDE.md)

## 🤝 贡献

欢迎提交Issue和Pull Request！

## 📄 许可证

MIT License

---

**bili_curator** - 让B站合集下载变得简单而智能 🎯
