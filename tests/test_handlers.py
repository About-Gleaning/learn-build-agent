import os
import base64
from pathlib import Path

import pytest

from agent.core.context import set_session_id
from agent.tools.bash_tool import (
    DEFAULT_TIMEOUT,
    PersistentBashSession,
    resolve_bash_workdir,
    run_bash,
    validate_readonly_bash,
)
from agent.tools.glob_tool import MAX_GLOB_RESULTS, resolve_glob_search_path, run_glob
from agent.tools.handlers import (
    build_plan_placeholder_path,
    build_tool_failure,
    build_tool_success,
    is_allowed_plan_write_path,
    run_edit,
    run_plan_enter,
    run_plan_exit,
    run_write,
)
from agent.tools.read_file_tool import resolve_readable_file_path, run_read
from agent.tools.todo_manager import TodoManager
from agent.runtime.workspace import (
    build_plan_storage_path,
    build_session_storage_name,
    build_todo_storage_path,
    configure_workspace,
    get_workspace,
)


def _set_test_session(session_id: str = "test_handler_session") -> str:
    return set_session_id(session_id)


def test_build_plan_placeholder_path_should_be_absolute():
    path = build_plan_placeholder_path("s:1/test")
    assert path.is_absolute()
    assert path == build_plan_storage_path("s:1/test")


def test_run_plan_enter_should_return_confirmation_required_when_unconfirmed():
    result = run_plan_enter(
        current_mode="build",
        plan_path="/tmp/p.md",
        plan_exists=False,
        latest_model="qwen-plus",
    )
    assert result["metadata"]["status"] == "confirmation_required"
    assert result["metadata"]["target_agent"] == "plan"


def test_run_plan_enter_should_return_confirmation_without_llm_confirmation_flag():
    result = run_plan_enter(
        current_mode="build",
        plan_path="/tmp/p.md",
        plan_exists=False,
        latest_model="qwen-plus",
    )
    assert result["metadata"]["status"] == "confirmation_required"
    assert result["metadata"]["action_type"] == "enter_plan"


def test_run_plan_enter_should_return_completed_when_already_in_plan_mode():
    result = run_plan_enter(
        current_mode="plan",
        plan_path="/tmp/p.md",
        plan_exists=False,
        latest_model="qwen-plus",
    )
    assert result["metadata"]["status"] == "completed"


def test_run_plan_exit_should_require_confirmation():
    result = run_plan_exit(
        current_mode="plan",
        plan_path="/tmp/p.md",
        plan_exists=True,
        latest_model="qwen-plus",
    )
    assert result["metadata"]["status"] == "confirmation_required"
    assert result["metadata"]["target_agent"] == "build"


def test_run_plan_exit_should_return_confirmation_without_llm_confirmation_flag():
    result = run_plan_exit(
        current_mode="plan",
        plan_path="/tmp/p.md",
        plan_exists=True,
        latest_model="qwen-plus",
    )
    assert result["metadata"]["status"] == "confirmation_required"
    assert result["metadata"]["action_type"] == "exit_plan"


def test_run_plan_exit_should_return_completed_when_not_in_plan():
    result = run_plan_exit(
        current_mode="build",
        plan_path="/tmp/p.md",
        plan_exists=True,
        latest_model="qwen-plus",
    )
    assert result["metadata"]["status"] == "completed"


def test_validate_readonly_bash_should_block_redirection():
    result = validate_readonly_bash("echo hello > /tmp/a.txt")
    assert result is not None
    assert "禁止重定向" in result


def test_validate_readonly_bash_should_allow_ls():
    result = validate_readonly_bash("ls -la")
    assert result is None


def test_validate_readonly_bash_should_allow_readonly_pipe():
    result = validate_readonly_bash('grep -n "start_backend" dev.sh | head -20')
    assert result is None


def test_validate_readonly_bash_should_block_non_whitelisted_pipe_command():
    result = validate_readonly_bash("cat README.md | xargs echo")
    assert result is not None
    assert "不允许执行命令 `xargs`" in result


def test_run_bash_should_use_default_timeout_and_workspace_root(tmp_path):
    configure_workspace(tmp_path)
    _set_test_session()
    result = run_bash("pwd")

    assert Path(result).resolve() == tmp_path.resolve()
    assert DEFAULT_TIMEOUT == 120000


def test_run_bash_should_support_custom_timeout(tmp_path):
    configure_workspace(tmp_path)
    _set_test_session()
    result = run_bash("sleep 0.2", timeout=50)

    assert result == "Error: Timeout (50ms)"


def test_run_bash_should_support_relative_workdir(tmp_path):
    configure_workspace(tmp_path)
    _set_test_session()
    nested_dir = tmp_path / "nested"
    nested_dir.mkdir()

    result = run_bash("pwd", workdir="nested")

    assert Path(result).resolve() == nested_dir.resolve()


def test_run_bash_should_not_keep_shell_state_between_calls(tmp_path):
    configure_workspace(tmp_path)
    _set_test_session()
    nested_dir = tmp_path / "nested"
    nested_dir.mkdir()

    assert run_bash("cd nested") == "(no output)"
    assert Path(run_bash("pwd")).resolve() == tmp_path.resolve()


def test_run_bash_should_not_keep_env_between_calls(tmp_path):
    configure_workspace(tmp_path)
    _set_test_session()

    assert run_bash("export DEMO_VAR=kept") == "(no output)"
    assert run_bash("printf '%s' \"$DEMO_VAR\"") == "(no output)"


def test_run_bash_should_share_shell_state_within_single_call(tmp_path):
    configure_workspace(tmp_path)
    _set_test_session()
    nested_dir = tmp_path / "nested"
    nested_dir.mkdir()

    result = run_bash("cd nested && pwd")

    assert Path(result).resolve() == nested_dir.resolve()


def test_run_bash_should_close_shell_after_timeout(tmp_path):
    configure_workspace(tmp_path)
    _set_test_session()

    result = run_bash("sleep 0.2", timeout=50)

    assert result == "Error: Timeout (50ms)"


def test_read_until_marker_should_detect_marker_across_chunks(tmp_path):
    session = PersistentBashSession(workdir=tmp_path)
    marker = "MARKER123"
    chunks = iter(["hello\nMARK", "ER123:0\n"])

    class FakeStdout:
        def fileno(self) -> int:
            return 1

    session.process = type("FakeProcess", (), {"stdout": FakeStdout()})()

    def fake_select(read_fds, write_fds, error_fds, timeout):
        del read_fds, write_fds, error_fds, timeout
        return ([1], [], [])

    def fake_read(fd, size):
        del fd, size
        try:
            return next(chunks).encode("utf-8")
        except StopIteration:
            return b""

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("agent.tools.bash_tool.select.select", fake_select)
    monkeypatch.setattr("agent.tools.bash_tool.os.read", fake_read)
    try:
        assert session._read_until_marker(marker, timeout_ms=1000) == "hello"
    finally:
        monkeypatch.undo()


def test_resolve_bash_workdir_should_allow_absolute_path_inside_workspace(tmp_path):
    configure_workspace(tmp_path)
    _set_test_session()
    nested_dir = tmp_path / "nested"
    nested_dir.mkdir()

    assert resolve_bash_workdir(str(nested_dir)) == nested_dir


def test_resolve_bash_workdir_should_reject_outside_workspace(tmp_path):
    configure_workspace(tmp_path)
    _set_test_session()

    with pytest.raises(ValueError, match="超出工作区范围"):
        resolve_bash_workdir("/tmp")


def test_resolve_bash_workdir_should_reject_missing_directory(tmp_path):
    configure_workspace(tmp_path)
    _set_test_session()

    with pytest.raises(FileNotFoundError, match="workdir 不存在"):
        resolve_bash_workdir("missing")


def test_resolve_bash_workdir_should_reject_file_path(tmp_path):
    configure_workspace(tmp_path)
    _set_test_session()
    file_path = tmp_path / "sample.txt"
    file_path.write_text("hello", encoding="utf-8")

    with pytest.raises(NotADirectoryError, match="workdir 不是目录"):
        resolve_bash_workdir("sample.txt")


def test_resolve_glob_search_path_should_default_to_workspace_root(tmp_path):
    configure_workspace(tmp_path)
    _set_test_session()

    assert resolve_glob_search_path() == tmp_path.resolve()


def test_run_glob_should_return_no_files_found_when_empty(tmp_path):
    configure_workspace(tmp_path)
    _set_test_session()

    result = run_glob("**/*.py")

    assert result["title"] == "."
    assert result["output"] == "No files found"
    assert result["metadata"]["status"] == "completed"
    assert result["metadata"]["count"] == 0
    assert result["metadata"]["truncated"] is False


def test_run_glob_should_sort_files_by_mtime_desc_and_filter_directories(tmp_path):
    configure_workspace(tmp_path)
    _set_test_session()
    src_dir = tmp_path / "src"
    nested_dir = src_dir / "nested"
    nested_dir.mkdir(parents=True)
    old_file = src_dir / "old.py"
    new_file = nested_dir / "new.py"
    old_file.write_text("old", encoding="utf-8")
    new_file.write_text("new", encoding="utf-8")
    os.utime(old_file, (1_700_000_000, 1_700_000_000))
    os.utime(new_file, (1_700_000_100, 1_700_000_100))

    result = run_glob("**/*", "src")

    assert result["title"] == "src"
    assert result["metadata"]["count"] == 2
    assert result["metadata"]["truncated"] is False
    assert result["output"].splitlines() == [str(new_file.resolve()), str(old_file.resolve())]


def test_run_glob_should_reject_path_outside_workspace(tmp_path):
    configure_workspace(tmp_path)
    _set_test_session()

    result = run_glob("**/*.py", "/tmp")

    assert result["metadata"]["status"] == "failed"
    assert result["metadata"]["error_code"] == "glob_path_forbidden"


def test_run_glob_should_reject_missing_directory(tmp_path):
    configure_workspace(tmp_path)
    _set_test_session()

    result = run_glob("**/*.py", "missing")

    assert result["metadata"]["status"] == "failed"
    assert result["metadata"]["error_code"] == "glob_path_not_found"


def test_run_glob_should_reject_non_directory_path(tmp_path):
    configure_workspace(tmp_path)
    _set_test_session()
    file_path = tmp_path / "demo.txt"
    file_path.write_text("demo", encoding="utf-8")

    result = run_glob("**/*.py", "demo.txt")

    assert result["metadata"]["status"] == "failed"
    assert result["metadata"]["error_code"] == "glob_path_not_directory"


def test_run_glob_should_truncate_to_latest_limit(tmp_path):
    configure_workspace(tmp_path)
    _set_test_session()

    for index in range(MAX_GLOB_RESULTS + 5):
        file_path = tmp_path / f"file_{index:03d}.py"
        file_path.write_text(str(index), encoding="utf-8")
        timestamp = 1_700_000_000 + index
        os.utime(file_path, (timestamp, timestamp))

    result = run_glob("*.py")
    output_lines = result["output"].splitlines()

    assert result["metadata"]["status"] == "completed"
    assert result["metadata"]["count"] == MAX_GLOB_RESULTS
    assert result["metadata"]["truncated"] is True
    assert output_lines[0].endswith(f"file_{MAX_GLOB_RESULTS + 4:03d}.py")
    assert output_lines[MAX_GLOB_RESULTS - 1].endswith("file_005.py")
    assert output_lines[-1] == "... truncated, omitted 5 older matches"


def test_is_allowed_plan_write_path():
    configure_workspace()
    session_id = _set_test_session("test_plan_session")
    expected_path = build_plan_storage_path(session_id)
    assert is_allowed_plan_write_path(str(expected_path))
    assert not is_allowed_plan_write_path(str(expected_path.parent / "other.md"))
    assert not is_allowed_plan_write_path("src/main.py")


def test_build_tool_success_should_mark_completed():
    result = build_tool_success("ok", path="a.txt")

    assert result["output"] == "ok"
    assert result["metadata"]["status"] == "completed"
    assert result["metadata"]["path"] == "a.txt"


def test_build_tool_failure_should_mark_failed():
    result = build_tool_failure("Error: bad", error_code="bad_request", detail="x")

    assert result["output"] == "Error: bad"
    assert result["metadata"]["status"] == "failed"
    assert result["metadata"]["error_code"] == "bad_request"
    assert result["metadata"]["detail"] == "x"


def test_run_read_should_return_structured_success(monkeypatch, tmp_path):
    file_path = tmp_path / "sample.txt"
    file_path.write_text("a\nb\nc\nd\n", encoding="utf-8")
    configure_workspace(tmp_path)
    _set_test_session()

    result = run_read(str(file_path), limit=2, offset=1)

    assert result["metadata"]["status"] == "completed"
    assert result["output"] == "b\nc\n... (1 more lines)"
    assert result["metadata"]["file_path"] == str(file_path.resolve())
    assert result["metadata"]["is_empty"] is False


def test_run_read_should_return_explicit_success_for_empty_file(tmp_path):
    file_path = tmp_path / "empty.txt"
    file_path.write_text("", encoding="utf-8")
    configure_workspace(tmp_path)
    _set_test_session()

    result = run_read(str(file_path))

    assert result["metadata"]["status"] == "completed"
    assert result["output"] == "文件存在，但内容为空。"
    assert result["metadata"]["file_path"] == str(file_path.resolve())
    assert result["metadata"]["is_empty"] is True


def test_run_read_should_return_pdf_attachment(monkeypatch, tmp_path):
    pdf_path = tmp_path / "demo.pdf"
    pdf_bytes = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n"
    pdf_path.write_bytes(pdf_bytes)
    configure_workspace(tmp_path)
    _set_test_session()

    result = run_read(str(pdf_path), limit=2, offset=1)

    assert result["metadata"]["status"] == "completed"
    assert result["output"] == "PDF read successfully"
    assert result["metadata"]["paging_ignored"] is True
    assert result["attachments"][0]["type"] == "file"
    assert result["attachments"][0]["mime"] == "application/pdf"
    assert result["attachments"][0]["url"] == (
        "data:application/pdf;base64," + base64.b64encode(pdf_bytes).decode("ascii")
    )


def test_run_read_should_fail_when_pdf_file_is_too_large(monkeypatch, tmp_path):
    pdf_path = tmp_path / "too-large.pdf"
    pdf_path.write_bytes(b"pdf")
    configure_workspace(tmp_path)
    _set_test_session()

    monkeypatch.setattr("agent.tools.read_file_tool.base64.b64encode", lambda data: b"x" * 10)
    monkeypatch.setattr("pathlib.Path.read_bytes", lambda self: b"x" * (50 * 1024 * 1024))

    result = run_read(str(pdf_path))

    assert result["metadata"]["status"] == "failed"
    assert result["metadata"]["error_code"] == "pdf_file_too_large"


def test_run_read_should_reject_relative_path(tmp_path):
    file_path = tmp_path / "sample.txt"
    file_path.write_text("hello", encoding="utf-8")
    configure_workspace(tmp_path)
    _set_test_session()

    result = run_read("sample.txt")

    assert result["metadata"]["status"] == "failed"
    assert result["metadata"]["error_code"] == "read_path_must_be_absolute"


def test_run_read_should_allow_current_session_plan_file(tmp_path):
    configure_workspace(tmp_path)
    session_id = _set_test_session("session-plan")
    plan_path = build_plan_storage_path(session_id)
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text("plan body", encoding="utf-8")

    result = run_read(str(plan_path))

    assert result["metadata"]["status"] == "completed"
    assert result["output"] == "plan body"


def test_run_read_should_allow_current_session_tool_output_file(tmp_path):
    configure_workspace(tmp_path)
    session_id = _set_test_session("session-tool-output")
    session_dir = get_workspace().tool_output_root / build_session_storage_name(session_id)
    session_dir.mkdir(parents=True, exist_ok=True)
    output_file = session_dir / "demo.log"
    output_file.write_text("tool output", encoding="utf-8")

    result = run_read(str(output_file))

    assert result["metadata"]["status"] == "completed"
    assert result["output"] == "tool output"


def test_run_read_should_allow_current_session_history_file(tmp_path):
    configure_workspace(tmp_path)
    session_id = _set_test_session("session-history")
    history_path = get_workspace().sessions_dir / build_session_storage_name(session_id, suffix=".json")
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text('{"messages":[]}', encoding="utf-8")

    result = run_read(str(history_path))

    assert result["metadata"]["status"] == "completed"
    assert result["output"] == '{"messages":[]}'


def test_run_read_should_reject_other_session_runtime_file(tmp_path):
    configure_workspace(tmp_path)
    _set_test_session("session-a")
    other_history_path = get_workspace().sessions_dir / build_session_storage_name("session-b", suffix=".json")
    other_history_path.parent.mkdir(parents=True, exist_ok=True)
    other_history_path.write_text('{"messages":[1]}', encoding="utf-8")

    result = run_read(str(other_history_path))

    assert result["metadata"]["status"] == "failed"
    assert result["metadata"]["error_code"] == "read_path_forbidden"


def test_resolve_readable_file_path_should_allow_workspace_absolute_path(tmp_path):
    file_path = tmp_path / "sample.txt"
    file_path.write_text("hello", encoding="utf-8")
    configure_workspace(tmp_path)
    _set_test_session()

    assert resolve_readable_file_path(str(file_path)) == file_path.resolve()


def test_run_write_should_return_structured_success(monkeypatch, tmp_path):
    configure_workspace(tmp_path)
    _set_test_session()

    result = run_write("notes.txt", "hello")

    assert result["metadata"]["status"] == "completed"
    assert result["output"] == "Wrote 5 bytes to notes.txt"
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "hello"


def test_run_edit_should_return_text_not_found_failure(monkeypatch, tmp_path):
    file_path = tmp_path / "sample.txt"
    file_path.write_text("hello", encoding="utf-8")
    configure_workspace(tmp_path)
    _set_test_session()

    result = run_edit("sample.txt", "missing", "world")

    assert result["metadata"]["status"] == "failed"
    assert result["metadata"]["error_code"] == "text_not_found"
    assert "Text not found" in result["output"]


def test_build_plan_placeholder_path_should_anchor_to_workspace_plan_path(tmp_path):
    project_root = tmp_path / "project-root"
    project_root.mkdir()
    configure_workspace(project_root)

    path = build_plan_placeholder_path("session:plan")

    assert path == build_plan_storage_path("session:plan").resolve()

def test_todo_manager_should_default_to_workspace_runtime_home(tmp_path):
    project_root = tmp_path / "project-root"
    project_root.mkdir()
    configure_workspace(project_root)

    manager = TodoManager()

    assert manager.storage_dir == get_workspace().todo_root


def test_todo_manager_should_build_session_scoped_storage_path(tmp_path):
    project_root = tmp_path / "project-root"
    project_root.mkdir()
    configure_workspace(project_root)

    manager = TodoManager()

    assert manager._session_file("session:todo") == build_todo_storage_path("session:todo")


def test_todo_manager_should_reject_stringified_todo_list(tmp_path):
    project_root = tmp_path / "project-root"
    project_root.mkdir()
    configure_workspace(project_root)
    _set_test_session("test_todo_string")
    manager = TodoManager()

    with pytest.raises(ValueError, match="todo_list 必须是 JSON array，不能是字符串"):
        manager.update('[{"id":"task1","text":"a","status":"pending","priority":"high"}]')  # type: ignore[arg-type]


def test_todo_manager_should_accept_single_todo_item(tmp_path):
    project_root = tmp_path / "project-root"
    project_root.mkdir()
    configure_workspace(project_root)
    _set_test_session("test_todo_single")
    manager = TodoManager()

    result = manager.update([
        {
            "id": "task1",
            "text": "搜索 hello.py 文件位置",
            "status": "completed",
            "priority": "high",
        }
    ])

    assert "[x] #task1: 搜索 hello.py 文件位置 (priority=high)" in result
