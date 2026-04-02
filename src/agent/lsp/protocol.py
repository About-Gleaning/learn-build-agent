from __future__ import annotations

import json
import queue
import threading
from subprocess import Popen
from typing import Any, Callable


class JsonRpcProtocolError(RuntimeError):
    pass


class JsonRpcEndpoint:
    """最小可用的 LSP JSON-RPC 通道，负责 request/notification 收发。"""

    def __init__(self, process: Popen[bytes], *, notification_handler: Callable[[str, Any], None]) -> None:
        if process.stdin is None or process.stdout is None:
            raise ValueError("LSP 进程必须提供 stdin/stdout 管道。")
        self._process = process
        self._stdin = process.stdin
        self._stdout = process.stdout
        self._notification_handler = notification_handler
        self._write_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._pending: dict[int, queue.Queue[dict[str, Any]]] = {}
        self._next_id = 1
        self._closed = False
        self._reader = threading.Thread(target=self._reader_loop, name=f"lsp-reader-{process.pid}", daemon=True)
        self._reader.start()

    @property
    def pid(self) -> int | None:
        return self._process.pid

    def is_alive(self) -> bool:
        return not self._closed and self._process.poll() is None

    def request(self, method: str, params: dict[str, Any], *, timeout_ms: int) -> Any:
        request_id: int
        response_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
        with self._pending_lock:
            request_id = self._next_id
            self._next_id += 1
            self._pending[request_id] = response_queue
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        try:
            response = response_queue.get(timeout=max(timeout_ms, 1) / 1000)
        except queue.Empty as exc:
            with self._pending_lock:
                self._pending.pop(request_id, None)
            raise TimeoutError(f"LSP 请求超时: {method}") from exc
        if "error" in response:
            raise JsonRpcProtocolError(str(response["error"]))
        return response.get("result")

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def close(self) -> None:
        self._closed = True
        try:
            if self._process.poll() is None:
                self._process.terminate()
        except Exception:
            pass

    def _send(self, message: dict[str, Any]) -> None:
        payload = json.dumps(message, ensure_ascii=False).encode("utf-8")
        header = f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii")
        with self._write_lock:
            self._stdin.write(header)
            self._stdin.write(payload)
            self._stdin.flush()

    def _reader_loop(self) -> None:
        try:
            while not self._closed:
                message = self._read_message()
                if message is None:
                    break
                if "id" in message and ("result" in message or "error" in message):
                    response_id = int(message["id"])
                    with self._pending_lock:
                        pending = self._pending.pop(response_id, None)
                    if pending is not None:
                        pending.put(message)
                    continue
                method = message.get("method")
                if isinstance(method, str):
                    self._notification_handler(method, message.get("params"))
        finally:
            self._closed = True
            with self._pending_lock:
                pending_items = list(self._pending.values())
                self._pending.clear()
            for pending in pending_items:
                pending.put({"error": {"message": "LSP 通道已关闭"}})

    def _read_message(self) -> dict[str, Any] | None:
        headers: dict[str, str] = {}
        while True:
            line = self._stdout.readline()
            if not line:
                return None
            if line in {b"\r\n", b"\n"}:
                break
            decoded = line.decode("ascii", errors="ignore").strip()
            if ":" not in decoded:
                continue
            key, value = decoded.split(":", 1)
            headers[key.strip().lower()] = value.strip()
        length_text = headers.get("content-length")
        if not length_text:
            raise JsonRpcProtocolError("LSP 消息缺少 Content-Length。")
        body = self._stdout.read(int(length_text))
        if not body:
            return None
        return json.loads(body.decode("utf-8"))
