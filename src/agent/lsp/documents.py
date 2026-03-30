from __future__ import annotations

import threading
import time
from pathlib import Path

from .types import DocumentSnapshot, build_file_uri


class DocumentStore:
    """按 server_key + normalized_path 维护 LSP 文档视角的状态。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._documents: dict[str, dict[str, DocumentSnapshot]] = {}

    def get(self, server_key: str, file_path: Path) -> DocumentSnapshot | None:
        normalized_path = str(file_path.resolve())
        with self._lock:
            return self._documents.get(server_key, {}).get(normalized_path)

    def open_document(self, server_key: str, file_path: Path, *, language_id: str, text: str) -> DocumentSnapshot:
        snapshot = DocumentSnapshot(
            file_path=str(file_path.resolve()),
            uri=build_file_uri(file_path),
            language_id=language_id,
            version=1,
            current_text=text,
            opened=True,
            last_synced_at_ns=time.time_ns(),
        )
        with self._lock:
            self._documents.setdefault(server_key, {})[snapshot.file_path] = snapshot
        return snapshot

    def update_document(self, server_key: str, file_path: Path, *, text: str) -> DocumentSnapshot:
        normalized_path = str(file_path.resolve())
        with self._lock:
            current = self._documents.get(server_key, {}).get(normalized_path)
            if current is None:
                raise KeyError(f"未找到文档状态: {server_key} {normalized_path}")
            snapshot = DocumentSnapshot(
                file_path=current.file_path,
                uri=current.uri,
                language_id=current.language_id,
                version=current.version + 1,
                current_text=text,
                opened=True,
                last_synced_at_ns=time.time_ns(),
            )
            self._documents.setdefault(server_key, {})[normalized_path] = snapshot
        return snapshot

    def clear_server(self, server_key: str) -> None:
        with self._lock:
            self._documents.pop(server_key, None)

    def clear(self) -> None:
        with self._lock:
            self._documents.clear()


_DOCUMENT_STORE = DocumentStore()


def get_document_store() -> DocumentStore:
    return _DOCUMENT_STORE


def clear_document_store() -> None:
    _DOCUMENT_STORE.clear()
