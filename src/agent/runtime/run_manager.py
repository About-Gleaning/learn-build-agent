from __future__ import annotations

import logging
import queue
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any, Literal

from ..core.message import utc_now_iso

logger = logging.getLogger(__name__)

RunStatus = Literal["running", "completed", "failed", "cancelled"]

_SENTINEL = object()


@dataclass
class RunRecord:
    """Web 后台执行单元，SSE 订阅者只消费事件，不承载执行生命周期。"""

    run_id: str
    session_id: str
    client_run_id: str = ""
    request_kind: str = "chat"
    status: RunStatus = "running"
    created_at: str = field(default_factory=utc_now_iso)
    completed_at: str = ""
    error: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)
    subscribers: list[queue.Queue[dict[str, Any] | object]] = field(default_factory=list)
    thread: threading.Thread | None = None
    last_touched_at: float = field(default_factory=time.monotonic)


class RunConflictError(RuntimeError):
    """同一 session 已有正在执行的后台任务。"""


class RunManager:
    """轻量内存 Run 管理器。

    设计目标是隔离 HTTP/SSE 连接生命周期：客户端断开只移除订阅者，
    后台线程继续完整消费 agent loop，确保工具执行和落库不被中断。
    """

    def __init__(
        self,
        *,
        max_events_per_run: int = 1000,
        subscriber_queue_size: int = 256,
        completed_ttl_seconds: float = 600,
        heartbeat_interval_seconds: float = 15,
    ) -> None:
        self._max_events_per_run = max_events_per_run
        self._subscriber_queue_size = subscriber_queue_size
        self._completed_ttl_seconds = completed_ttl_seconds
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._runs: dict[str, RunRecord] = {}
        self._active_by_session: dict[str, str] = {}
        self._run_by_client_id: dict[tuple[str, str], str] = {}
        self._lock = threading.RLock()

    def create_run(
        self,
        *,
        session_id: str,
        event_source: Callable[[], Iterable[dict[str, Any]]],
    ) -> RunRecord:
        normalized_session_id = (session_id or "").strip()
        if not normalized_session_id:
            raise ValueError("session_id 不能为空")

        with self._lock:
            self._prune_locked()
            active_run_id = self._active_by_session.get(normalized_session_id, "")
            if active_run_id:
                active_run = self._runs.get(active_run_id)
                if active_run is not None and active_run.status == "running":
                    raise RunConflictError(f"当前会话已有任务正在执行：{active_run_id}")
                self._active_by_session.pop(normalized_session_id, None)

            run = RunRecord(run_id=f"run_{uuid.uuid4().hex[:12]}", session_id=normalized_session_id)
            self._runs[run.run_id] = run
            self._active_by_session[normalized_session_id] = run.run_id

        thread = threading.Thread(
            target=self._run_event_source,
            args=(run.run_id, event_source),
            name=f"agent-run-{run.run_id}",
            daemon=True,
        )
        run.thread = thread
        thread.start()
        return run

    def create_or_get_run(
        self,
        *,
        session_id: str,
        client_run_id: str,
        event_source: Callable[[], Iterable[dict[str, Any]]],
        request_kind: str = "chat",
    ) -> RunRecord:
        """按前端幂等键创建或复用 run，避免断线重连造成重复提交。"""
        normalized_session_id = (session_id or "").strip()
        normalized_client_run_id = (client_run_id or "").strip()
        if not normalized_session_id:
            raise ValueError("session_id 不能为空")
        if not normalized_client_run_id:
            raise ValueError("client_run_id 不能为空")

        with self._lock:
            self._prune_locked()
            client_key = (normalized_session_id, normalized_client_run_id)
            existing_run_id = self._run_by_client_id.get(client_key, "")
            if existing_run_id:
                existing_run = self._runs.get(existing_run_id)
                if existing_run is not None:
                    existing_run.last_touched_at = time.monotonic()
                    return existing_run
                self._run_by_client_id.pop(client_key, None)

            active_run_id = self._active_by_session.get(normalized_session_id, "")
            if active_run_id:
                active_run = self._runs.get(active_run_id)
                if active_run is not None and active_run.status == "running":
                    raise RunConflictError(f"当前会话已有任务正在执行：{active_run_id}")
                self._active_by_session.pop(normalized_session_id, None)

            run = RunRecord(
                run_id=f"run_{uuid.uuid4().hex[:12]}",
                session_id=normalized_session_id,
                client_run_id=normalized_client_run_id,
                request_kind=(request_kind or "chat").strip() or "chat",
            )
            self._runs[run.run_id] = run
            self._active_by_session[normalized_session_id] = run.run_id
            self._run_by_client_id[client_key] = run.run_id

        thread = threading.Thread(
            target=self._run_event_source,
            args=(run.run_id, event_source),
            name=f"agent-run-{run.run_id}",
            daemon=True,
        )
        run.thread = thread
        thread.start()
        return run

    def subscribe(self, run_id: str) -> Iterator[dict[str, Any]]:
        subscriber: queue.Queue[dict[str, Any] | object] = queue.Queue(maxsize=self._subscriber_queue_size)
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise KeyError(f"未知 run_id：{run_id}")
            run.last_touched_at = time.monotonic()
            for event in run.events:
                self._offer_event(subscriber, event)
            if run.status == "running":
                run.subscribers.append(subscriber)
            else:
                self._offer_sentinel(subscriber)

        try:
            while True:
                try:
                    item = subscriber.get(timeout=self._heartbeat_interval_seconds)
                except queue.Empty:
                    heartbeat = self._build_heartbeat(run_id)
                    if heartbeat is None:
                        break
                    yield heartbeat
                    continue
                if item is _SENTINEL:
                    break
                if isinstance(item, dict):
                    yield item
        finally:
            with self._lock:
                run = self._runs.get(run_id)
                if run is not None:
                    run.last_touched_at = time.monotonic()
                    run.subscribers = [item for item in run.subscribers if item is not subscriber]

    def get_run(self, run_id: str) -> RunRecord | None:
        with self._lock:
            return self._runs.get(run_id)

    def get_run_by_client_id(self, session_id: str, client_run_id: str) -> RunRecord | None:
        normalized_session_id = (session_id or "").strip()
        normalized_client_run_id = (client_run_id or "").strip()
        if not normalized_session_id or not normalized_client_run_id:
            return None

        with self._lock:
            self._prune_locked()
            run_id = self._run_by_client_id.get((normalized_session_id, normalized_client_run_id), "")
            if not run_id:
                return None
            run = self._runs.get(run_id)
            if run is None:
                self._run_by_client_id.pop((normalized_session_id, normalized_client_run_id), None)
                return None
            run.last_touched_at = time.monotonic()
            return run

    def get_active_run(self, session_id: str) -> RunRecord | None:
        normalized_session_id = (session_id or "").strip()
        if not normalized_session_id:
            return None

        with self._lock:
            self._prune_locked()
            active_run_id = self._active_by_session.get(normalized_session_id, "")
            if not active_run_id:
                return None
            active_run = self._runs.get(active_run_id)
            if active_run is not None and active_run.status == "running":
                active_run.last_touched_at = time.monotonic()
                return active_run
            # 索引可能因为异常路径残留；查询时顺手修正，避免后续创建 run 被误判冲突。
            self._active_by_session.pop(normalized_session_id, None)
            return None

    def clear(self) -> None:
        """测试辅助：清空内存 Run 状态，不影响 session JSONL。"""
        with self._lock:
            self._runs.clear()
            self._active_by_session.clear()
            self._run_by_client_id.clear()

    def _run_event_source(self, run_id: str, event_source: Callable[[], Iterable[dict[str, Any]]]) -> None:
        final_status: RunStatus = "completed"
        try:
            saw_terminal = False
            for event in event_source():
                if not isinstance(event, dict):
                    continue
                event_type = str(event.get("type", ""))
                if event_type == "error":
                    saw_terminal = True
                    final_status = "failed"
                if event_type == "done" and int(event.get("depth", 0) or 0) == 0:
                    saw_terminal = True
                    status = str(event.get("status", "")).strip().lower()
                    if status in {"failed", "error"}:
                        final_status = "failed"
                    elif status in {"interrupted", "cancelled"}:
                        final_status = "cancelled"
                self.publish(run_id, event)
            if not saw_terminal:
                final_status = "failed"
                self.publish(
                    run_id,
                    {
                        "type": "error",
                        "code": "run_missing_terminal_event",
                        "message": "后台任务结束但未产生终态事件。",
                        "session_id": self._session_id_for_run(run_id),
                    },
                )
        except Exception as exc:
            final_status = "failed"
            logger.exception("run_manager.background_failed run_id=%s", run_id)
            self.publish(
                run_id,
                {
                    "type": "error",
                    "code": "internal_error",
                    "message": str(exc),
                    "session_id": self._session_id_for_run(run_id),
                },
            )
        finally:
            self.finish(run_id, final_status)

    def publish(self, run_id: str, event: dict[str, Any]) -> None:
        event_payload = dict(event)
        event_payload.setdefault("run_id", run_id)
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return
            run.last_touched_at = time.monotonic()
            run.events.append(event_payload)
            if len(run.events) > self._max_events_per_run:
                del run.events[: len(run.events) - self._max_events_per_run]
            subscribers = list(run.subscribers)

        for subscriber in subscribers:
            self._offer_event(subscriber, event_payload)

    def finish(self, run_id: str, status: RunStatus, error: str = "") -> None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return
            if run.status != "running":
                return
            run.status = status
            run.completed_at = utc_now_iso()
            run.error = error
            run.last_touched_at = time.monotonic()
            self._active_by_session.pop(run.session_id, None)
            subscribers = list(run.subscribers)
            run.subscribers.clear()

        for subscriber in subscribers:
            self._offer_sentinel(subscriber)

    def _session_id_for_run(self, run_id: str) -> str:
        with self._lock:
            run = self._runs.get(run_id)
            return run.session_id if run is not None else ""

    def _build_heartbeat(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None or run.status != "running":
                return None
            run.last_touched_at = time.monotonic()
            payload: dict[str, Any] = {
                "type": "heartbeat",
                "run_id": run.run_id,
                "session_id": run.session_id,
                "timestamp": utc_now_iso(),
            }
            if run.client_run_id:
                payload["client_run_id"] = run.client_run_id
            return payload

    def _prune_locked(self) -> None:
        now = time.monotonic()
        expired_run_ids = [
            run_id
            for run_id, run in self._runs.items()
            if run.status != "running" and now - run.last_touched_at > self._completed_ttl_seconds
        ]
        for run_id in expired_run_ids:
            run = self._runs.pop(run_id, None)
            if run is not None:
                self._active_by_session.pop(run.session_id, None)
                if run.client_run_id:
                    self._run_by_client_id.pop((run.session_id, run.client_run_id), None)

    def _offer_event(self, target_queue: queue.Queue[dict[str, Any] | object], event: dict[str, Any]) -> None:
        try:
            target_queue.put_nowait(event)
            return
        except queue.Full:
            pass
        try:
            target_queue.get_nowait()
        except queue.Empty:
            pass
        try:
            target_queue.put_nowait(event)
        except queue.Full:
            logger.warning("run_manager.subscriber_queue_drop")

    def _offer_sentinel(self, target_queue: queue.Queue[dict[str, Any] | object]) -> None:
        try:
            target_queue.put_nowait(_SENTINEL)
            return
        except queue.Full:
            pass
        try:
            target_queue.get_nowait()
        except queue.Empty:
            pass
        try:
            target_queue.put_nowait(_SENTINEL)
        except queue.Full:
            logger.warning("run_manager.subscriber_sentinel_drop")
