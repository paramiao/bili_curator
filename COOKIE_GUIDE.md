# B站Cookie使用指南

## 🍪 什么是Cookie？

Cookie是浏览器存储的用户认证信息，使用Cookie可以：
- 下载需要登录权限的视频
- 获得更高质量的视频格式
- 避免某些访问限制
- 提高下载成功率

## 🔑 如何获取SESSDATA？

### 方法1：浏览器开发者工具（推荐）

1. **打开B站并登录**
2. **按F12打开开发者工具**
3. **切换到"Application"或"存储"标签**
4. **在左侧找到"Cookies" → "https://www.bilibili.com"**
5. **找到名为"SESSDATA"的Cookie**
6. **复制其值**

### 方法2：浏览器地址栏

1. **登录B站后，在地址栏输入：**
   ```javascript
   javascript:alert(document.cookie.match(/SESSDATA=([^;]+)/)[1])
   ```
2. **按回车，会弹出SESSDATA值**

### 方法3：浏览器扩展

使用Cookie导出扩展，如"Get cookies.txt"等。

## 📝 使用方法

### 单个合集下载

```bash
# 方法1：直接指定SESSDATA
python bilibili_collection_downloader_v4.py \
  "合集URL" \
  "./downloads" \
  --cookies "SESSDATA=66b7bf77%2C1770468788%2Cb735f%2A81CjDSbHHzy6WMttmU2eHPr0MTeOve6vrO86MCLjDoUxEGIuhGkJ6nacYAldqhqKT1yxISVlpVMkVRblk5QXVJV0lrQy1jQW45bXh3a3lSRk5LU1FlU1dIcHZrMW1LNlo4azRRS2hCeWFCMGcydlVTR2phWTcyU29mTkZSakZvOF9YUEZRUVNfMnRRIIEC" \
  --max-videos 10

# 方法2：使用Cookie文件
python bilibili_collection_downloader_v4.py \
  "合集URL" \
  "./downloads" \
  --cookies-file cookies.txt \
  --max-videos 10

# 方法3：多个Cookie
python bilibili_collection_downloader_v4.py \
  "合集URL" \
  "./downloads" \
  --cookies "SESSDATA=xxx; buvid3=yyy; bili_jct=zzz" \
  --max-videos 10
```

### 批量下载

```bash
# 全局Cookie（应用于所有合集）
python batch_download_v4.py \
  collections_config_v4.json \
  ./downloads \
  --cookies "SESSDATA=你的SESSDATA"

# 使用Cookie文件
python batch_download_v4.py \
  collections_config_v4.json \
  ./downloads \
  --cookies-file cookies.txt
```

## 📄 Cookie文件格式

创建 `cookies.txt` 文件：

```
# Netscape HTTP Cookie File
.bilibili.com	TRUE	/	FALSE	0	SESSDATA	你的SESSDATA值
.bilibili.com	TRUE	/	FALSE	0	buvid3	你的buvid3值
.bilibili.com	TRUE	/	FALSE	0	bili_jct	你的bili_jct值
```

## ⚙️ 配置文件中的Cookie

在配置文件中为特定合集指定Cookie：

```json
[
  {
    "name": "普通合集",
    "url": "https://space.bilibili.com/xxx/lists/xxx?type=season",
    "use_auto_name": true,
    "max_videos": 50
  },
  {
    "name": "需要登录的合集",
    "url": "https://space.bilibili.com/xxx/lists/xxx?type=season",
    "use_auto_name": true,
    "max_videos": 20,
    "cookies": "SESSDATA=专用的SESSDATA值"
  }
]
```

## 🔒 Cookie安全注意事项

### ⚠️ 重要提醒

1. **Cookie是敏感信息**：包含您的登录状态，请妥善保管
2. **不要分享Cookie**：避免泄露给他人
3. **定期更新**：Cookie有过期时间，失效后需要重新获取
4. **使用完毕删除**：建议使用后删除临时Cookie文件

### 🛡️ 安全建议

- 使用专门的下载账号
- 定期更换密码
- 不在公共电脑上获取Cookie
- 使用完毕后清理Cookie文件

## 🚨 常见问题

### Q: Cookie失效怎么办？
A: 重新登录B站，按照上述方法重新获取SESSDATA

### Q: 下载失败提示认证错误？
A: 检查Cookie是否正确，是否已过期

### Q: 可以使用他人的Cookie吗？
A: 不建议，可能违反使用条款，且存在安全风险

### Q: Cookie文件在哪里？
A: 脚本会在输出目录创建临时Cookie文件，使用完毕后自动删除

## 📊 Cookie优势对比

| 功能 | 无Cookie | 有Cookie |
|------|----------|----------|
| 公开视频 | ✅ | ✅ |
| 会员专享 | ❌ | ✅ |
| 高清画质 | 部分 | ✅ |
| 下载速度 | 一般 | 更快 |
| 成功率 | 较低 | 更高 |

## 🎯 最佳实践

1. **首次使用**：先不用Cookie测试，确认脚本正常工作
2. **需要时添加**：遇到下载失败或画质不佳时再添加Cookie
3. **定期检查**：定期验证Cookie是否仍然有效
4. **备份重要**：对重要的Cookie进行安全备份

## 📞 技术支持

如果在使用Cookie过程中遇到问题：
1. 检查Cookie格式是否正确
2. 确认Cookie未过期
3. 尝试重新获取Cookie
4. 检查网络连接和B站访问状态

