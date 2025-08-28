# 功能模块页面

这些是 bili_curator 的专项管理工具页面，作为主SPA应用的功能补充。

## 📁 页面说明

| 页面文件 | 功能用途 | 访问路径 |
|----------|----------|----------|
| `queue_admin.html` | 请求队列管理 | `/static/queue_admin.html` |
| `strm_management.html` | STRM流媒体配置 | `/static/strm_management.html` |
| `subscription_detail.html` | 订阅同步详情 | `/static/subscription_detail.html` |
| `video_detection.html` | 视频检测工具 | `/static/video_detection.html` |
| `test.html` | 开发测试页面 | `/static/test.html` |

## 🎯 架构角色

- **定位**: 独立的管理工具页面
- **关系**: SPA应用的功能补充，非主应用架构
- **用途**: 专项配置、调试、监控等高级功能
- **访问**: 直接URL访问或从主SPA导航

## 🔗 与主SPA的关系

```
主SPA应用 (/)
    ↓ 统一导航
功能模块页面 (/static/*)
    ↓ 专项工具
后端API (/api/*)
```

## 🛠️ 技术特点

- 原生HTML + JavaScript实现
- 独立页面，无路由依赖
- 直接调用后端API
- 响应式CSS设计
- 轻量级工具页面

## 📝 开发说明

这些页面主要用于：
1. **高级管理功能** - 需要专门界面的复杂操作
2. **开发调试工具** - 开发和运维使用的诊断页面
3. **实验性功能** - 新功能的独立测试页面

---

**重要**: 这些不是主应用入口，主入口是 `/` 路径的SPA应用。
