#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="$ROOT_DIR/.runtime"
BACKEND_PID_FILE="$RUNTIME_DIR/backend.pid"
FRONTEND_PID_FILE="$RUNTIME_DIR/frontend.pid"
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

start_backend() {
  if is_service_running "$BACKEND_PID_FILE"; then
    echo "后端已在运行，PID=$(read_pid "$BACKEND_PID_FILE")"
    return
  fi

  echo "正在启动后端..."
  (
    cd "$ROOT_DIR"
    nohup uvicorn src.web_main:app --reload --host "$BACKEND_HOST" --port "$BACKEND_PORT" >"$BACKEND_LOG_FILE" 2>&1 &
    echo $! > "$BACKEND_PID_FILE"
  )

  sleep 1
  if is_service_running "$BACKEND_PID_FILE"; then
    echo "后端启动成功: http://$BACKEND_HOST:$BACKEND_PORT (PID=$(read_pid "$BACKEND_PID_FILE"))"
  else
    echo "后端启动失败，请检查日志: $BACKEND_LOG_FILE"
    tail -n 40 "$BACKEND_LOG_FILE" || true
    exit 1
  fi
}

start_frontend() {
  if is_service_running "$FRONTEND_PID_FILE"; then
    echo "前端已在运行，PID=$(read_pid "$FRONTEND_PID_FILE")"
    return
  fi

  echo "正在启动前端..."
  (
    cd "$ROOT_DIR/frontend"
    nohup pnpm dev --host "$FRONTEND_HOST" --port "$FRONTEND_PORT" >"$FRONTEND_LOG_FILE" 2>&1 &
    echo $! > "$FRONTEND_PID_FILE"
  )

  sleep 1
  if is_service_running "$FRONTEND_PID_FILE"; then
    echo "前端启动成功: http://$FRONTEND_HOST:$FRONTEND_PORT (PID=$(read_pid "$FRONTEND_PID_FILE"))"
  else
    echo "前端启动失败，请检查日志: $FRONTEND_LOG_FILE"
    tail -n 40 "$FRONTEND_LOG_FILE" || true
    exit 1
  fi
}

stop_service() {
  local name="$1"
  local pid_file="$2"

  local pid
  pid="$(read_pid "$pid_file")"
  if ! is_pid_running "$pid"; then
    rm -f "$pid_file"
    echo "$name 未运行"
    return
  fi

  echo "正在停止$name，PID=$pid ..."
  kill "$pid" >/dev/null 2>&1 || true

  for _ in {1..20}; do
    if ! is_pid_running "$pid"; then
      rm -f "$pid_file"
      echo "$name 已停止"
      return
    fi
    sleep 0.2
  done

  echo "$name 停止超时，执行强制结束..."
  kill -9 "$pid" >/dev/null 2>&1 || true
  rm -f "$pid_file"
  echo "$name 已强制停止"
}

show_status() {
  if is_service_running "$BACKEND_PID_FILE"; then
    echo "后端运行中: http://$BACKEND_HOST:$BACKEND_PORT (PID=$(read_pid "$BACKEND_PID_FILE"))"
  else
    echo "后端未运行"
  fi

  if is_service_running "$FRONTEND_PID_FILE"; then
    echo "前端运行中: http://$FRONTEND_HOST:$FRONTEND_PORT (PID=$(read_pid "$FRONTEND_PID_FILE"))"
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
    stop_service "前端" "$FRONTEND_PID_FILE"
    stop_service "后端" "$BACKEND_PID_FILE"
    ;;
  restart)
    stop_service "前端" "$FRONTEND_PID_FILE"
    stop_service "后端" "$BACKEND_PID_FILE"
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
