# 遗留问题与注意事项（Known Issues）

更新时间：2025-08-14 21:42 (Asia/Shanghai)

## 1. 已知问题
- `/api/tasks/{task_id}/status` 与 `/api/tasks` 的数据源/索引不完全一致，偶发“任务不存在”。
- 历史目录名与订阅名不一致时，目录边界匹配可能遗漏：需先对齐目录或暂时修改订阅名再“自动关联”。
- Cookie 失效（401/403）仍需用户更换 Cookie；后端计划按失败阈值后自动禁用，避免偶发 401/403 误伤（进行中）。
- 统计刷新节奏：`expected-total` 已采用与下载一致的分段统计；前端已增加 1 小时本地缓存与手动刷新，但在极短时间内重复刷新仍可能触发风控，请酌情操作。

## 2. 风险点
- 容器未重启导致旧代码生效：新增端点或修复可能未加载。
- 跨合集误判：已通过“目录内去重/关联”修复，但需确保订阅目录命名统一。
- 大规模扫描性能：全盘扫描会慢；当前已限定为订阅目录，仍建议合理分层目录结构。
- 轻量迁移依赖容器重启：`download_tasks` 的列补齐/迁移在应用启动时执行，未重启可能沿用旧表结构。

## 3. 排障建议
- 查看订阅任务与日志：`GET /api/subscriptions/{id}/tasks`；容器日志 `docker logs -f bili_curator_v6`。
- 统计异常：先执行 `auto-import/scan` 与 `auto-import/associate`，再刷新订阅列表。
- 下载秒完成：升级到包含“目录内去重”修复的版本，重启容器后重试。
- 使用 docker compose 时指定文件查看日志：`docker compose -f bili_curator_v6/docker-compose.yml logs -f`

## 4. 已修复问题（近期）
- `GET /api/media/subscription-stats` 500：由 SQLAlchemy `func.case(..., else_=...)` 用法不兼容导致，已改为 `sqlalchemy.case`（2025-08-14）。
- 前端接口异常导致“加载中…”卡住：已统一在 `apiRequest()` 对 4xx/5xx/非JSON 做友好处理，并在订阅/目录统计模块展示错误提示。

## 5. 后续修复计划
- 统一任务状态端点；
- 订阅仪表盘聚合与 SSE 实时推送；
- 前端家用友好配置与显著错误提示（Cookie/权限/网络）。
 - 对 412/风控响应做仅告警不计失败的全局处理；将分段大小/延时/整体轻量重试暴露为配置。
