#!/usr/bin/env bash
set -euo pipefail

# One-click manager for bili_curator (Docker Compose)
# Usage:
#   ./scripts/manage.sh up|down|restart|rebuild|logs|ps|health
# Env:
#   COMPOSE_FILE (optional): path to compose file. Default: bili_curator_v6/docker-compose.yml
#   CONFIG_DIR (optional): host config dir. Default: ~/bilibili_config

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE_DEFAULT="$REPO_ROOT/bili_curator_v6/docker-compose.yml"
COMPOSE_FILE_PATH="${COMPOSE_FILE:-$COMPOSE_FILE_DEFAULT}"
CONFIG_DIR_PATH="${CONFIG_DIR:-$HOME/bilibili_config}"

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
  diag       Diagnose API consistency across endpoints (in container)

Environment:
  COMPOSE_FILE   Path to docker-compose file (default: $COMPOSE_FILE_DEFAULT)
  CONFIG_DIR     Host config dir for DB/logs (default: $HOME/bilibili_config)
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
    diag) cmd_diag ;;
    -h|--help|help|"") usage ;;
    *) echo "Unknown command: $cmd"; usage; exit 2 ;;
  esac
}

main "$@"
