# STRM播放问题排查指南

## 🔍 概述

本文档提供bili_curator V7 STRM流媒体功能的完整故障排除指南，涵盖播放器兼容性、网络问题和常见错误的诊断方法。

## 📊 技术架构验证

### 1. 容器健康检查
```bash
# 检查容器状态
docker ps | grep bili_curator
docker logs bili_curator_v7 --tail 50

# 健康检查端点
curl -f http://localhost:8080/health
```

### 2. STRM文件格式验证
```bash
# 检查STRM文件内容
find /path/to/strm -name "*.strm" | head -1 | xargs cat
# 应显示: http://IP:8080/api/strm/stream/BV1234567890.mp4

# 检查文件权限
ls -la /path/to/strm/
```

### 3. API端点测试
```bash
# 测试基本响应
curl -I "http://localhost:8080/api/strm/stream/BV1234567890.mp4"
# 期望: HTTP/1.1 200 OK, Content-Type: video/mp4

# 测试Range请求支持
curl -I -H "Range: bytes=0-1023" "http://localhost:8080/api/strm/stream/BV1234567890.mp4"
# 期望: HTTP/1.1 206 Partial Content, Content-Range: bytes 0-1023/总大小
```

## 🎯 播放器兼容性分析

### 支持Range请求的播放器
- **VLC Media Player**: 完全支持，推荐使用
- **MPV**: 完全支持，性能优秀
- **Emby/Jellyfin**: 完全支持，媒体服务器首选
- **Kodi**: 完全支持

### 不支持Range请求的播放器
- **senplayer**: 直接请求完整文件，可能导致启动延迟
- **部分老旧播放器**: 可能需要完整下载后播放

### 播放器请求模式对比
```
# 现代播放器 (VLC/MPV/Emby)
🔍 STRM请求 - 方法: HEAD, Range: bytes=0-1023
🔍 HEAD 206响应 - Range: 0-1023, Length: 1024

# senplayer
🔍 STRM请求 - 方法: GET, Range: None
🔍 serve_local_file - 完整文件流, 状态码: 200
```

## 🔧 常见问题排查

### 1. Emby/Jellyfin无法播放

**症状**: STRM文件被识别但无法播放，显示网络错误

**排查步骤**:
```bash
# 1. 从媒体服务器测试连通性
curl -I "http://bili_curator_ip:8080/api/strm/stream/BV1234567890.mp4"

# 2. 检查网络配置
# Emby设置 -> 网络 -> 本地网络地址
# 确保bili_curator的IP在本地网络范围内

# 3. 检查Docker网络
docker network ls
docker inspect emby_container | grep NetworkMode
```

**解决方案**:
- 确保Emby服务器可访问bili_curator的8080端口
- 在Emby网络设置中添加bili_curator IP到本地网络
- 如果都在Docker中，确保在同一网络或可互相访问

### 2. STRM文件无法识别

**症状**: 媒体服务器扫描不到STRM文件

**排查步骤**:
```bash
# 检查文件结构
ls -la /strm/path/
# 应包含: .strm, .nfo, .jpg 文件

# 检查权限
chmod 644 /strm/path/*.strm
chmod 644 /strm/path/*.nfo
chmod 644 /strm/path/*.jpg
```

### 3. 播放启动缓慢

**症状**: 点击播放后等待时间过长

**可能原因**:
- 播放器不支持Range请求（如senplayer）
- 网络延迟较高
- B站CDN响应慢

**优化建议**:
- 使用支持Range请求的播放器
- 启用本地缓存功能
- 选择较低质量进行测试

### 4. 502 Bad Gateway错误

**症状**: 播放时显示502错误

**排查步骤**:
```bash
# 检查容器日志
docker logs bili_curator_v7 | grep -E "(ERROR|502|Exception)"

# 检查B站API连通性
docker exec bili_curator_v7 curl -I "https://api.bilibili.com"

# 检查Cookie配置
# 确保BILIBILI_SESSDATA等环境变量正确配置
```

## 📋 诊断清单

### 基础验证 ✅
- [ ] 容器运行正常且健康
- [ ] STRM文件URL格式正确
- [ ] API端点返回200状态码
- [ ] Range请求返回206状态码
- [ ] 缓存文件存在且权限正确

### 网络连通性 ✅
- [ ] 播放器可访问bili_curator:8080
- [ ] 防火墙允许8080端口
- [ ] Docker网络配置正确
- [ ] 媒体服务器网络设置正确

### 播放器配置 ✅
- [ ] 播放器支持STRM格式
- [ ] 网络播放功能已启用
- [ ] 转码设置合理
- [ ] 缓存设置适当

## 🛠️ 高级调试

### 启用详细日志
```bash
# 设置调试级别
docker exec bili_curator_v7 env LOG_LEVEL=DEBUG

# 实时监控日志
docker logs bili_curator_v7 -f | grep -E "(STRM|serve_local_file|Range)"
```

### 网络抓包分析
```bash
# 使用tcpdump监控8080端口
sudo tcpdump -i any port 8080 -A

# 分析HTTP请求头
curl -v "http://localhost:8080/api/strm/stream/BV1234567890.mp4"
```

## 📞 技术支持

如果按照本指南仍无法解决问题，请提供以下信息：

1. **环境信息**: 操作系统、Docker版本、播放器版本
2. **错误日志**: 容器日志和播放器错误信息
3. **网络配置**: IP地址、端口映射、防火墙设置
4. **测试结果**: curl命令的完整输出

## 🔄 更新记录

- **2025-09-04**: 初始版本，基于V7.0.0 STRM功能
- **2025-09-04**: 添加senplayer兼容性分析和播放器对比
