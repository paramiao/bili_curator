# 使用 Docker 一键部署/启停

更新时间：2025-08-15 17:54 (Asia/Shanghai)

本项目提供脚本 `scripts/manage.sh` 进行一键启停、重启、重建、日志查看与健康检查。

## 脚本使用

```bash
# 赋予执行权限（首次）
chmod +x scripts/manage.sh

# 启动/重启（如需构建会自动构建）
./scripts/manage.sh up

# 查看日志
./scripts/manage.sh logs

# 查看服务状态
./scripts/manage.sh ps

# 健康检查
./scripts/manage.sh health

# 停止并移除容器
./scripts/manage.sh down

# 强制重建镜像并重建容器
./scripts/manage.sh rebuild
```

支持的环境变量：
- `COMPOSE_FILE`：指定 compose 文件路径，默认 `bili_curator_v6/docker-compose.yml`
- `CONFIG_DIR`：主机的配置目录（数据库/日志），默认 `~/bilibili_config`

## docker-compose 关键点

参见 `bili_curator_v6/docker-compose.yml`：
- 服务名：`bili-curator`，容器名：`bili_curator_v6`
- 端口：`8080:8080`
- 卷挂载：
  - `/Volumes/nas-mk/xiaoya_emby/xiaoya/bilibili:/app/downloads`
  - `~/bilibili_config:/app/data`
  - `~/bilibili_config/logs:/app/logs`
  - `./static:/app/static:ro`
  - `./web:/app/web:ro`
  - `./app:/app/app:ro`
  - `./import_existing_videos.py:/app/import_existing_videos.py:ro`
  - `./main.py:/app/main.py:ro`

## 运行时环境变量（建议）

在容器环境中（由应用读取）：
- `DOWNLOAD_PATH=/app/downloads`
- `DB_PATH=/app/data/bilibili_curator.db`
- `TZ=Asia/Shanghai`
- 超时配置（建议值）：
  - `EXPECTED_TOTAL_TIMEOUT=30`
  - `LIST_MAX_CHUNKS=200`
  - `LIST_FETCH_CMD_TIMEOUT=120`
  - `DOWNLOAD_CMD_TIMEOUT=1800`
  - `META_CMD_TIMEOUT=60`

## 快速验证

- 访问健康检查：`curl -s http://localhost:8080/health`
- 自动导入再关联：
  - `curl -s -X POST http://localhost:8080/api/auto-import/scan`
  - `curl -s -X POST http://localhost:8080/api/auto-import/associate`
- 刷新远端总数（快速路径，不枚举）：
  - `curl -s http://localhost:8080/api/subscriptions/1/expected-total`

更多 API 与操作见 `README.md`、`docs/BACKEND_IMPLEMENTATION.md`、`docs/KNOWN_ISSUES.md`。
