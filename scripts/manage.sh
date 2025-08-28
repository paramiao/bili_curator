#!/usr/bin/env bash
set -euo pipefail

# One-click manager for bili_curator (Docker Compose)
# Usage:
#   ./scripts/manage.sh up|down|restart|rebuild|logs|ps|health|strm|backup
#   VERSION=v7 ./scripts/manage.sh up    # 部署V7版本
# Env:
#   VERSION (optional): v6 or v7. Default: v7 (STRM支持)
#   COMPOSE_FILE (optional): path to compose file. Default: docker-compose.yml
#   CONFIG_DIR (optional): host config dir. Default: ~/bilibili_config

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE_DEFAULT="$REPO_ROOT/docker-compose.yml"
COMPOSE_FILE_PATH="${COMPOSE_FILE:-$COMPOSE_FILE_DEFAULT}"
CONFIG_DIR_PATH="${CONFIG_DIR:-$HOME/bilibili_config}"
# Ensure CONFIG_HOST_PATH is exported for docker-compose.yml defaults
export CONFIG_HOST_PATH="${CONFIG_HOST_PATH:-$CONFIG_DIR_PATH}"

# Optional: load .env if present (non-intrusive)
if [ -f "$REPO_ROOT/.env" ]; then
  set +u
  set -o allexport
  # shellcheck disable=SC1090
  . "$REPO_ROOT/.env"
  set +o allexport
  set -u
fi

ensure_prereqs() {
  command -v docker >/dev/null 2>&1 || { echo "[ERROR] docker not found"; exit 1; }
  command -v docker compose >/dev/null 2>&1 || { echo "[ERROR] docker compose plugin not found"; exit 1; }
  if [[ ! -f "$COMPOSE_FILE_PATH" ]]; then
    echo "[ERROR] compose file not found: $COMPOSE_FILE_PATH"; exit 1
  fi
}

ensure_dirs() {
  mkdir -p "$CONFIG_DIR_PATH" "$CONFIG_DIR_PATH/logs"
}

cmd_up() {
  ensure_prereqs; ensure_dirs
  docker compose -f "$COMPOSE_FILE_PATH" up -d
  echo "[INFO] Waiting for health (timeout 60s)..."
  set +e
  for i in {1..60}; do
    if curl -fsS http://localhost:8080/health >/dev/null 2>&1; then
      echo "[OK] Service healthy"
      ok=1
      break
    fi
    sleep 1
  done
  if [ -z "${ok:-}" ]; then
    echo "[WARN] Health not ready within 60s. Check logs: ./scripts/manage.sh logs"
  fi
  set -e
  echo "[OK] Up/Restarted. See logs: ./scripts/manage.sh logs"
}

cmd_down() {
  ensure_prereqs
  docker compose -f "$COMPOSE_FILE_PATH" down
}

cmd_restart() {
  ensure_prereqs
  docker compose -f "$COMPOSE_FILE_PATH" restart
}

cmd_rebuild() {
  ensure_prereqs; ensure_dirs
  docker compose -f "$COMPOSE_FILE_PATH" build --no-cache
  docker compose -f "$COMPOSE_FILE_PATH" up -d --force-recreate
}

cmd_logs() {
  ensure_prereqs
  docker compose -f "$COMPOSE_FILE_PATH" logs -f --tail=200
}

cmd_ps() {
  ensure_prereqs
  docker compose -f "$COMPOSE_FILE_PATH" ps
}

cmd_health() {
  ensure_prereqs
  set +e
  curl -fsS http://localhost:8080/health && echo "" && echo "[OK] Health endpoint returned 200" && exit 0
  status=$?
  echo "[WARN] Health check failed with status $status"; exit $status
}

cmd_dev() {
  ensure_prereqs; ensure_dirs
  local DEV_COMPOSE="$REPO_ROOT/docker-compose.dev.yml"
  if [[ ! -f "$DEV_COMPOSE" ]]; then
    echo "[ERROR] dev compose override not found: $DEV_COMPOSE"; exit 1
  fi
  docker compose -f "$COMPOSE_FILE_PATH" -f "$DEV_COMPOSE" up -d
  echo "[OK] Dev mode up. Hot-reload/static mounts enabled. See logs: ./scripts/manage.sh logs"
}

cmd_strm() {
  ensure_prereqs
  echo "[INFO] STRM功能状态检查..."
  set +e
  echo "# STRM前置检查"
  echo "- ffmpeg 可用性（容器内）"
  docker compose -f "$COMPOSE_FILE_PATH" exec -T bili-curator sh -lc 'command -v ffmpeg >/dev/null 2>&1 && ffmpeg -version | head -n1 || echo "ffmpeg not found"'
  echo "- BILIBILI_SESSDATA 配置（容器内，仅检查是否设置）"
  docker compose -f "$COMPOSE_FILE_PATH" exec -T bili-curator sh -lc '[ -n "$BILIBILI_SESSDATA" ] && echo "SESSDATA: set" || echo "SESSDATA: not set"'
  
  # 检查STRM健康状态
  echo "# STRM健康检查"
  if curl -fsS http://localhost:8080/api/strm/health >/dev/null 2>&1; then
    curl -s http://localhost:8080/api/strm/health | python3 -m json.tool 2>/dev/null || curl -s http://localhost:8080/api/strm/health
  else
    echo "STRM健康检查失败 - 服务可能未启动或STRM功能未启用"
  fi
  
  echo -e "\n# STRM统计信息"
  if curl -fsS http://localhost:8080/api/strm/stats/streams >/dev/null 2>&1; then
    curl -s http://localhost:8080/api/strm/stats/streams | python3 -m json.tool 2>/dev/null || curl -s http://localhost:8080/api/strm/stats/streams
  else
    echo "无法获取STRM统计信息"
  fi
  
  echo -e "\n# STRM文件统计"
  if curl -fsS http://localhost:8080/api/strm/stats/files >/dev/null 2>&1; then
    curl -s http://localhost:8080/api/strm/stats/files | python3 -m json.tool 2>/dev/null || curl -s http://localhost:8080/api/strm/stats/files
  else
    echo "无法获取STRM文件统计"
  fi
  
  echo -e "\n[INFO] STRM管理界面: http://localhost:8080/static/strm_management.html"
}

cmd_backup() {
  ensure_prereqs; ensure_dirs
  local BK_DIR="$CONFIG_DIR_PATH/logs/backups"
  mkdir -p "$BK_DIR"
  local TS
  TS="$(date +%Y%m%d_%H%M%S)"
  # Detect DB_PATH inside container with fallback
  local CONTAINER_DB
  CONTAINER_DB=$(docker compose -f "$COMPOSE_FILE_PATH" exec -T bili-curator sh -lc 'printf "%s" "${DB_PATH:-/app/data/bilibili_curator.db}"' 2>/dev/null || true)
  if [ -z "$CONTAINER_DB" ]; then
    CONTAINER_DB="/app/data/bilibili_curator.db"
  fi
  local DEST="$BK_DIR/bilibili_curator.db.$TS"
  echo "[INFO] Backing up DB from $CONTAINER_DB to $DEST"
  if docker compose -f "$COMPOSE_FILE_PATH" cp bili-curator:"$CONTAINER_DB" "$DEST"; then
    echo "[OK] Backup completed: $DEST"
  else
    echo "[ERROR] Backup failed. Ensure service is running and DB exists at $CONTAINER_DB" >&2
    exit 1
  fi
}

cmd_diag() {
  ensure_prereqs
  echo "[INFO] Running in-container diagnostics via docker compose exec ..."
  docker compose -f "$COMPOSE_FILE_PATH" exec bili-curator sh -lc '
    set -e
    echo "# Health";
    if curl -fsS http://localhost:8080/health >/dev/null; then echo "OK"; else echo "FAIL"; fi
    echo "\n# Subscriptions (preview)";
    curl -s http://localhost:8080/api/subscriptions | head -c 1200; echo
    echo "\n# Aggregate";
    curl -s http://localhost:8080/api/download/aggregate | head -c 1200; echo
    echo "\n# Overview";
    curl -s http://localhost:8080/api/overview | head -c 1200; echo
  '
}

usage() {
  cat <<EOF
Usage: $(basename "$0") <command>

Commands:
  up         Build (if needed) and start in background
  down       Stop and remove containers
  restart    Restart services
  rebuild    Rebuild image without cache and recreate
  logs       Tail compose logs
  ps         Show compose services status
  health     Call http://localhost:8080/health
  strm       Check STRM streaming feature status and stats
  backup     Backup SQLite DB from container to host logs/backups/
  diag       Diagnose API consistency across endpoints (in container)
  dev        Start with docker-compose.dev.yml override for local development

Environment:
  VERSION        Version to deploy: v6 or v7 (default: v7)
  COMPOSE_FILE   Path to docker-compose file (default: $COMPOSE_FILE_DEFAULT)
  CONFIG_DIR     Host config dir for DB/logs (default: $HOME/bilibili_config)

V7 STRM Features:
  - STRM management UI: http://localhost:8080/static/strm_management.html
  - Dual mode support: LOCAL (download) + STRM (streaming)
  - 99% storage savings with STRM mode
EOF
}

main() {
  local cmd=${1:-}
  case "$cmd" in
    up) cmd_up ;;
    down) cmd_down ;;
    restart) cmd_restart ;;
    rebuild) cmd_rebuild ;;
    logs) cmd_logs ;;
    ps) cmd_ps ;;
    health) cmd_health ;;
    strm) cmd_strm ;;
    backup) cmd_backup ;;
    diag) cmd_diag ;;
    dev) cmd_dev ;;
    -h|--help|help|"") usage ;;
    *) echo "Unknown command: $cmd"; usage; exit 2 ;;
  esac
}

main "$@"
