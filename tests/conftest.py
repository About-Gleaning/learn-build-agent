import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
os.environ.setdefault("CODEPILOT_HOME", "/tmp/codepilot-test-home")

for path in (ROOT, SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


@pytest.fixture(autouse=True)
def isolate_workspace_runtime(monkeypatch, tmp_path):
    from agent.runtime import workspace as workspace_module

    runtime_home = tmp_path / ".codepilot"
    monkeypatch.setenv("CODEPILOT_HOME", str(runtime_home))
    monkeypatch.setattr(workspace_module, "DEFAULT_RUNTIME_HOME", runtime_home)
    workspace_module.reset_workspace()
    yield
    workspace_module.reset_workspace()


@pytest.fixture(autouse=True)
def stub_task_artifact_ingest(monkeypatch):
    from agent.core.message import append_text_part, create_message
    from agent.runtime import task_artifacts

    def fake_empty_ingest(messages, tools, llm_config=None, agent=""):
        del tools, llm_config, agent
        session_id = messages[-1]["info"]["session_id"]
        response = create_message("assistant", session_id, status="completed", finish_reason="stop")
        append_text_part(response, '{"artifacts": []}')
        return response

    monkeypatch.setattr(task_artifacts, "create_chat_completion", fake_empty_ingest)
