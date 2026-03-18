import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
os.environ.setdefault("MY_AGENT_HOME", "/tmp/my-main-agent-test-home")

for path in (ROOT, SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


@pytest.fixture(autouse=True)
def isolate_workspace_runtime(monkeypatch, tmp_path):
    from agent.runtime import workspace as workspace_module

    monkeypatch.setattr(workspace_module, "DEFAULT_RUNTIME_HOME", tmp_path / ".my-agent")
    workspace_module.reset_workspace()
    yield
    workspace_module.reset_workspace()
