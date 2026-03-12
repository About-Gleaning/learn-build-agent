#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="$ROOT_DIR/.runtime"
BACKEND_PID_FILE="$RUNTIME_DIR/backend.pid"
FRONTEND_PID_FILE="$RUNTIME_DIR/frontend.pid"
FRONTEND_PORT_FILE="$RUNTIME_DIR/frontend.port"
BACKEND_LOG_FILE="$RUNTIME_DIR/backend.log"
FRONTEND_LOG_FILE="$RUNTIME_DIR/frontend.log"

BACKEND_HOST="127.0.0.1"
BACKEND_PORT="8000"
FRONTEND_HOST="127.0.0.1"
FRONTEND_PORT="5173"

mkdir -p "$RUNTIME_DIR"

print_usage() {
  cat <<'EOF'
用法: ./dev.sh <命令>

命令:
  start     启动前后端服务
  stop      停止前后端服务
  restart   重启前后端服务
  status    查看前后端服务状态
  logs      查看日志文件路径
EOF
}

read_pid() {
  local pid_file="$1"
  if [[ -f "$pid_file" ]]; then
    cat "$pid_file"
  fi
}

is_pid_running() {
  local pid="$1"
  if [[ -z "$pid" ]]; then
    return 1
  fi
  kill -0 "$pid" >/dev/null 2>&1
}

is_service_running() {
  local pid_file="$1"
  local pid
  pid="$(read_pid "$pid_file")"
  is_pid_running "$pid"
}

get_listener_pids_by_port() {
  local port="${1-}"
  if [[ -z "$port" ]]; then
    return 0
  fi
  lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null | awk '!seen[$0]++' || true
}

get_frontend_runtime_port() {
  if [[ -f "$FRONTEND_PORT_FILE" ]]; then
    cat "$FRONTEND_PORT_FILE"
    return 0
  fi

  if [[ -f "$FRONTEND_LOG_FILE" ]]; then
    python3 - "$FRONTEND_LOG_FILE" <<'PY'
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.exists():
    sys.exit(0)
text = path.read_text(encoding="utf-8", errors="ignore")
matches = re.findall(r"Local:\s+http://127\.0\.0\.1:(\d+)/", text)
if matches:
    print(matches[-1])
PY
  fi
}

persist_frontend_runtime_port() {
  local port=""
  port="$(python3 - "$FRONTEND_LOG_FILE" <<'PY'
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.exists():
    sys.exit(0)
text = path.read_text(encoding="utf-8", errors="ignore")
matches = re.findall(r"Local:\s+http://127\.0\.0\.1:(\d+)/", text)
if matches:
    print(matches[-1])
PY
)"
  if [[ -n "$port" ]]; then
    printf '%s\n' "$port" > "$FRONTEND_PORT_FILE"
  fi
}

find_backend_pids() {
  local pid=""
  pid="$(read_pid "$BACKEND_PID_FILE")"
  if is_pid_running "$pid"; then
    printf '%s\n' "$pid"
  fi
  get_listener_pids_by_port "$BACKEND_PORT" || true
}

find_frontend_pids() {
  local pid=""
  local port=""

  pid="$(read_pid "$FRONTEND_PID_FILE")"
  if is_pid_running "$pid"; then
    printf '%s\n' "$pid"
  fi

  port="$(get_frontend_runtime_port || true)"
  if [[ -n "$port" ]]; then
    get_listener_pids_by_port "$port" || true
    return
  fi

  get_listener_pids_by_port "$FRONTEND_PORT" || true
}

dedupe_pids() {
  awk 'NF && !seen[$0]++'
}

select_primary_pid() {
  awk 'NF { if ($1 > max) max = $1 } END { if (max) print max }'
}

format_pid_summary() {
  local pids="${1-}"
  if [[ -z "$pids" ]]; then
    return 0
  fi

  local primary_pid
  local count
  primary_pid="$(printf '%s\n' "$pids" | select_primary_pid)"
  count="$(printf '%s\n' "$pids" | awk 'NF { c++ } END { print c + 0 }')"

  if [[ "$count" -le 1 ]]; then
    printf '%s' "$primary_pid"
    return 0
  fi

  printf '%s (+%s)' "$primary_pid" "$((count - 1))"
}

start_backend_process() {
  local reload_enabled="${1-}"

  (
    cd "$ROOT_DIR"
    if [[ "$reload_enabled" == "1" ]]; then
      nohup uvicorn src.web_main:app --reload --host "$BACKEND_HOST" --port "$BACKEND_PORT" >"$BACKEND_LOG_FILE" 2>&1 &
    else
      nohup uvicorn src.web_main:app --host "$BACKEND_HOST" --port "$BACKEND_PORT" >"$BACKEND_LOG_FILE" 2>&1 &
    fi
    echo $! > "$BACKEND_PID_FILE"
  )
}

start_backend() {
  local existing_pids
  existing_pids="$(find_backend_pids | dedupe_pids)"
  if [[ -n "$existing_pids" ]]; then
    echo "后端已在运行，PID=$(format_pid_summary "$existing_pids")"
    return
  fi

  echo "正在启动后端..."
  : > "$BACKEND_LOG_FILE"
  start_backend_process "1"

  for _ in {1..20}; do
    if [[ -n "$(find_backend_pids | dedupe_pids)" ]]; then
      echo "后端启动成功: http://$BACKEND_HOST:$BACKEND_PORT (PID=$(format_pid_summary "$(find_backend_pids | dedupe_pids)"))"
      return
    fi
    sleep 0.3
  done

  if ! is_service_running "$BACKEND_PID_FILE"; then
    if grep -Eq "Operation not permitted|Permission denied|watchfiles|Will watch for changes" "$BACKEND_LOG_FILE"; then
      rm -f "$BACKEND_PID_FILE"
      echo "后端热重载启动失败，自动降级为非 --reload 模式..."
      : > "$BACKEND_LOG_FILE"
      start_backend_process "0"

      for _ in {1..20}; do
        if [[ -n "$(find_backend_pids | dedupe_pids)" ]]; then
          echo "后端启动成功(已关闭热重载): http://$BACKEND_HOST:$BACKEND_PORT (PID=$(format_pid_summary "$(find_backend_pids | dedupe_pids)"))"
          return
        fi
        sleep 0.3
      done
    fi

    echo "后端启动失败，请检查日志: $BACKEND_LOG_FILE"
    tail -n 40 "$BACKEND_LOG_FILE" || true
    exit 1
  fi
}

start_frontend() {
  local existing_pids
  existing_pids="$(find_frontend_pids | dedupe_pids)"
  if [[ -n "$existing_pids" ]]; then
    echo "前端已在运行，PID=$(format_pid_summary "$existing_pids")"
    return
  fi

  echo "正在启动前端..."
  rm -f "$FRONTEND_PORT_FILE"
  (
    cd "$ROOT_DIR/frontend"
    nohup pnpm dev --host "$FRONTEND_HOST" --port "$FRONTEND_PORT" >"$FRONTEND_LOG_FILE" 2>&1 &
    echo $! > "$FRONTEND_PID_FILE"
  )

  for _ in {1..30}; do
    persist_frontend_runtime_port
    local runtime_port
    runtime_port="$(get_frontend_runtime_port || true)"
    if [[ -n "$runtime_port" && -n "$(get_listener_pids_by_port "$runtime_port" | dedupe_pids)" ]]; then
      echo "前端启动成功: http://$FRONTEND_HOST:$runtime_port (PID=$(format_pid_summary "$(find_frontend_pids | dedupe_pids)"))"
      return
    fi
    if ! is_service_running "$FRONTEND_PID_FILE" && [[ -z "$runtime_port" ]]; then
      break
    fi
    sleep 0.3
  done

  echo "前端启动失败，请检查日志: $FRONTEND_LOG_FILE"
  tail -n 40 "$FRONTEND_LOG_FILE" || true
  exit 1
}

stop_service() {
  local service_name="${1-}"
  local pid_file="${2-}"
  local finder_name="${3-}"

  if [[ -z "$service_name" || -z "$pid_file" || -z "$finder_name" ]]; then
    printf '%s\n' "stop_service 参数缺失"
    return 1
  fi

  local pids
  pids="$($finder_name | dedupe_pids)"
  if [[ -z "$pids" ]]; then
    rm -f "$pid_file"
    if [[ "$service_name" == "前端" ]]; then
      rm -f "$FRONTEND_PORT_FILE"
    fi
    printf '%s\n' "${service_name} 未运行"
    return
  fi

  printf '%s\n' "正在停止 ${service_name}，PID=$(format_pid_summary "$pids") ..."
  while IFS= read -r pid; do
    [[ -n "$pid" ]] || continue
    kill "$pid" >/dev/null 2>&1 || true
  done <<< "$pids"

  for _ in {1..20}; do
    local remaining
    remaining="$($finder_name | dedupe_pids)"
    if [[ -z "$remaining" ]]; then
      rm -f "$pid_file"
      if [[ "$service_name" == "前端" ]]; then
        rm -f "$FRONTEND_PORT_FILE"
      fi
      printf '%s\n' "${service_name} 已停止"
      return
    fi
    sleep 0.2
  done

  printf '%s\n' "${service_name} 停止超时，执行强制结束..."
  while IFS= read -r pid; do
    [[ -n "$pid" ]] || continue
    kill -9 "$pid" >/dev/null 2>&1 || true
  done <<< "$pids"
  rm -f "$pid_file"
  if [[ "$service_name" == "前端" ]]; then
    rm -f "$FRONTEND_PORT_FILE"
  fi
  printf '%s\n' "${service_name} 已强制停止"
}

show_status() {
  local backend_pids
  local frontend_pids
  local frontend_runtime_port

  backend_pids="$(find_backend_pids | dedupe_pids)"
  frontend_pids="$(find_frontend_pids | dedupe_pids)"
  frontend_runtime_port="$(get_frontend_runtime_port || true)"
  if [[ -z "$frontend_runtime_port" ]]; then
    frontend_runtime_port="$FRONTEND_PORT"
  fi

  if [[ -n "$backend_pids" ]]; then
    echo "后端运行中: http://$BACKEND_HOST:$BACKEND_PORT (PID=$(format_pid_summary "$backend_pids"))"
  else
    echo "后端未运行"
  fi

  if [[ -n "$frontend_pids" ]]; then
    echo "前端运行中: http://$FRONTEND_HOST:$frontend_runtime_port (PID=$(format_pid_summary "$frontend_pids"))"
  else
    echo "前端未运行"
  fi
}

show_logs() {
  echo "后端日志: $BACKEND_LOG_FILE"
  echo "前端日志: $FRONTEND_LOG_FILE"
}

cmd="${1:-}"
case "$cmd" in
  start)
    start_backend
    start_frontend
    ;;
  stop)
    stop_service "前端" "$FRONTEND_PID_FILE" find_frontend_pids
    stop_service "后端" "$BACKEND_PID_FILE" find_backend_pids
    ;;
  restart)
    stop_service "前端" "$FRONTEND_PID_FILE" find_frontend_pids
    stop_service "后端" "$BACKEND_PID_FILE" find_backend_pids
    start_backend
    start_frontend
    ;;
  status)
    show_status
    ;;
  logs)
    show_logs
    ;;
  *)
    print_usage
    exit 1
    ;;
esac
