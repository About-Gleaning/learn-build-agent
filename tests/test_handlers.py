import os
import base64
from pathlib import Path

import pytest

from agent.lsp.types import LspDiagnostic, LspDiagnosticsResult, LspPosition, LspQueryResult, LspRange
from agent.core.context import set_session_id
from agent.tools.bash_tool import (
    DEFAULT_TIMEOUT,
    PersistentBashSession,
    resolve_bash_workdir,
    run_bash,
    validate_readonly_bash,
)
from agent.tools.grep_tool import MAX_GREP_RESULTS, resolve_grep_search_path, run_grep
from agent.tools.glob_tool import MAX_GLOB_RESULTS, resolve_glob_search_path, run_glob
from agent.tools.edit_file_tool import run_edit
from agent.tools.file_edit_state import clear_file_edit_states
from agent.tools.handlers import (
    build_plan_placeholder_path,
    build_tool_failure,
    build_tool_success,
    is_allowed_plan_write_path,
    run_plan_enter,
    run_plan_exit,
)
from agent.tools.question_tool import CUSTOM_OPTION_LABEL, run_question
from agent.tools.read_file_tool import resolve_readable_file_path, run_read
from agent.tools.lsp_tool import run_lsp
from agent.tools.skill_tool import run_load_skill
from agent.tools.todo_manager import TodoManager
from agent.tools.write_file_tool import run_write
from agent.skills.runtime import SkillRegistry
from agent.runtime.workspace import (
    build_plan_storage_path,
    build_session_storage_name,
    build_todo_storage_path,
    configure_workspace,
    get_workspace,
)


def _set_test_session(session_id: str = "test_handler_session") -> str:
    return set_session_id(session_id)


@pytest.fixture(autouse=True)
def _clear_edit_state():
    clear_file_edit_states()
    yield
    clear_file_edit_states()


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


def test_run_question_should_return_question_required():
    result = run_question(
        questions=[
            {
                "question": "选择实现方式？",
                "header": "实现方式",
                "options": [
                    {"label": "方案A", "description": "改动更小"},
                    {"label": "方案B", "description": "扩展性更好"},
                ],
            }
        ]
    )
    assert result["metadata"]["status"] == "question_required"
    assert result["metadata"]["questions"][0]["header"] == "实现方式"
    assert result["metadata"]["questions"][0]["custom"] is True
    assert result["metadata"]["questions"][0]["options"][-1]["label"] == CUSTOM_OPTION_LABEL


def test_run_question_should_not_append_custom_option_when_disabled():
    result = run_question(
        questions=[
            {
                "question": "选择实现方式？",
                "header": "实现方式",
                "custom": False,
                "options": [
                    {"label": "方案A", "description": "改动更小"},
                    {"label": "方案B", "description": "扩展性更好"},
                ],
            }
        ]
    )

    assert result["metadata"]["questions"][0]["custom"] is False
    assert [item["label"] for item in result["metadata"]["questions"][0]["options"]] == ["方案A", "方案B"]


def test_run_question_should_not_duplicate_custom_option():
    result = run_question(
        questions=[
            {
                "question": "选择实现方式？",
                "header": "实现方式",
                "options": [
                    {"label": "方案A", "description": "改动更小"},
                    {"label": CUSTOM_OPTION_LABEL, "description": "以上选项都不合适"},
                ],
            }
        ]
    )

    labels = [item["label"] for item in result["metadata"]["questions"][0]["options"]]
    assert labels.count(CUSTOM_OPTION_LABEL) == 1


def test_run_question_should_reject_invalid_questions():
    result = run_question(questions=[])
    assert result["metadata"]["status"] == "failed"
    assert result["metadata"]["error_code"] == "question_invalid"


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


def test_resolve_grep_search_path_should_default_to_workspace_root(tmp_path):
    configure_workspace(tmp_path)
    _set_test_session()

    assert resolve_grep_search_path() == tmp_path.resolve()


def test_run_grep_should_return_no_matches_when_empty(tmp_path):
    configure_workspace(tmp_path)
    _set_test_session()
    (tmp_path / "demo.py").write_text("print('hello')\n", encoding="utf-8")

    result = run_grep("UserService")

    assert result["title"] == "."
    assert result["output"] == "No matches found"
    assert result["metadata"]["status"] == "completed"
    assert result["metadata"]["count"] == 0
    assert result["metadata"]["truncated"] is False


def test_run_grep_should_sort_by_file_mtime_then_line_number(tmp_path):
    configure_workspace(tmp_path)
    _set_test_session()
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    old_file = src_dir / "old.py"
    new_file = src_dir / "new.py"
    old_file.write_text("target old one\ntarget old two\n", encoding="utf-8")
    new_file.write_text("target new one\ntarget new two\n", encoding="utf-8")
    os.utime(old_file, (1_700_000_000, 1_700_000_000))
    os.utime(new_file, (1_700_000_100, 1_700_000_100))

    result = run_grep("target", "src")

    assert result["title"] == "src"
    assert result["metadata"]["count"] == 4
    assert result["metadata"]["truncated"] is False
    assert result["output"].splitlines() == [
        f"{new_file.resolve()}:1:target new one",
        f"{new_file.resolve()}:2:target new two",
        f"{old_file.resolve()}:1:target old one",
        f"{old_file.resolve()}:2:target old two",
    ]


def test_run_grep_should_support_include_filters(tmp_path):
    configure_workspace(tmp_path)
    _set_test_session()
    (tmp_path / "a.py").write_text("needle\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("needle\n", encoding="utf-8")

    result = run_grep("needle", include=["*.py"])

    assert result["metadata"]["count"] == 1
    assert result["metadata"]["include"] == ["*.py"]
    assert str((tmp_path / "a.py").resolve()) in result["output"]
    assert str((tmp_path / "b.txt").resolve()) not in result["output"]


def test_run_grep_should_reject_invalid_include_type(tmp_path):
    configure_workspace(tmp_path)
    _set_test_session()

    result = run_grep("needle", include="*.py")

    assert result["metadata"]["status"] == "failed"
    assert result["metadata"]["error_code"] == "grep_pattern_invalid"


def test_run_grep_should_reject_path_outside_workspace(tmp_path):
    configure_workspace(tmp_path)
    _set_test_session()

    result = run_grep("needle", "/tmp")

    assert result["metadata"]["status"] == "failed"
    assert result["metadata"]["error_code"] == "grep_path_forbidden"


def test_run_grep_should_reject_missing_directory(tmp_path):
    configure_workspace(tmp_path)
    _set_test_session()

    result = run_grep("needle", "missing")

    assert result["metadata"]["status"] == "failed"
    assert result["metadata"]["error_code"] == "grep_path_not_found"


def test_run_grep_should_reject_non_directory_path(tmp_path):
    configure_workspace(tmp_path)
    _set_test_session()
    file_path = tmp_path / "demo.txt"
    file_path.write_text("needle\n", encoding="utf-8")

    result = run_grep("needle", "demo.txt")

    assert result["metadata"]["status"] == "failed"
    assert result["metadata"]["error_code"] == "grep_path_not_directory"


def test_run_grep_should_truncate_to_latest_limit(tmp_path):
    configure_workspace(tmp_path)
    _set_test_session()

    for index in range(MAX_GREP_RESULTS + 5):
        file_path = tmp_path / f"file_{index:03d}.py"
        file_path.write_text("needle\n", encoding="utf-8")
        timestamp = 1_700_000_000 + index
        os.utime(file_path, (timestamp, timestamp))

    result = run_grep("needle")
    output_lines = result["output"].splitlines()

    assert result["metadata"]["status"] == "completed"
    assert result["metadata"]["count"] == MAX_GREP_RESULTS
    assert result["metadata"]["truncated"] is True
    assert output_lines[0].endswith(f"file_{MAX_GREP_RESULTS + 4:03d}.py:1:needle")
    assert output_lines[MAX_GREP_RESULTS - 1].endswith("file_005.py:1:needle")
    assert output_lines[-1] == "... truncated, omitted 5 additional matches"


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


def test_run_read_should_allow_skills_file_under_runtime_home(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_AGENT_HOME", str(tmp_path / ".my-agent"))
    configure_workspace(tmp_path / "workspace")
    _set_test_session("skills-read")
    skill_file = get_workspace().skills_dir / "demo-skill" / "references" / "rule.md"
    skill_file.parent.mkdir(parents=True, exist_ok=True)
    skill_file.write_text("skill reference", encoding="utf-8")

    result = run_read(str(skill_file))

    assert result["metadata"]["status"] == "completed"
    assert result["output"] == "skill reference"


def test_run_lsp_should_resolve_relative_path_and_return_formatted_json(tmp_path, monkeypatch):
    configure_workspace(tmp_path)
    _set_test_session("lsp-relative")
    target = tmp_path / "src" / "demo.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("print('hi')\n", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_query_lsp(*, operation, file_path, content, line, character):
        captured.update(
            operation=operation,
            file_path=str(file_path),
            content=content,
            line=line,
            character=character,
        )
        return LspQueryResult(
            status="completed",
            operation=operation,
            result=[{"uri": file_path.as_uri()}],
            lsp_language="python",
            lsp_server="pylsp",
        )

    monkeypatch.setattr("agent.tools.lsp_tool.query_lsp", fake_query_lsp)

    result = run_lsp("goToDefinition", "src/demo.py", 1, 1)

    assert result["title"] == "goToDefinition src/demo.py:1:1"
    assert result["metadata"]["status"] == "completed"
    assert result["metadata"]["result"] == [{"uri": target.as_uri()}]
    assert '"uri": "' in result["output"]
    assert captured == {
        "operation": "goToDefinition",
        "file_path": str(target),
        "content": "print('hi')\n",
        "line": 0,
        "character": 0,
    }


def test_run_lsp_should_return_no_results_message(tmp_path, monkeypatch):
    configure_workspace(tmp_path)
    _set_test_session("lsp-empty")
    target = tmp_path / "demo.py"
    target.write_text("print('hi')", encoding="utf-8")

    monkeypatch.setattr(
        "agent.tools.lsp_tool.query_lsp",
        lambda **kwargs: LspQueryResult(status="completed", operation=kwargs["operation"], result=[]),
    )

    result = run_lsp("findReferences", str(target), 1, 1)

    assert result["metadata"]["status"] == "completed"
    assert result["output"] == "No results found for findReferences"


def test_run_lsp_should_fail_for_out_of_range_position(tmp_path):
    configure_workspace(tmp_path)
    _set_test_session("lsp-position")
    target = tmp_path / "demo.py"
    target.write_text("print('hi')", encoding="utf-8")

    result = run_lsp("hover", str(target), 3, 1)

    assert result["title"] == "hover demo.py:3:1"
    assert result["metadata"]["status"] == "failed"
    assert result["metadata"]["error_code"] == "lsp_position_invalid"


def test_run_lsp_should_fail_for_query_error_status(tmp_path, monkeypatch):
    configure_workspace(tmp_path)
    _set_test_session("lsp-failed")
    target = tmp_path / "Foo.java"
    target.write_text("class Foo {}", encoding="utf-8")

    monkeypatch.setattr(
        "agent.tools.lsp_tool.query_lsp",
        lambda **kwargs: LspQueryResult(
            status="operation_unsupported",
            operation=kwargs["operation"],
            lsp_error="当前 LSP 不支持 outgoingCalls",
            lsp_language="java",
            lsp_server="jdtls",
        ),
    )

    result = run_lsp("outgoingCalls", str(target), 1, 1)

    assert result["metadata"]["status"] == "failed"
    assert result["metadata"]["error_code"] == "lsp_operation_unsupported"
    assert "当前 LSP 不支持 outgoingCalls" in result["output"]


def test_run_load_skill_should_return_structured_success(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_AGENT_HOME", str(tmp_path / ".my-agent"))
    configure_workspace(tmp_path / "workspace")
    _set_test_session("skills-load")
    skill_dir = get_workspace().skills_dir / "demo-skill"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: demo\n---\n# Demo Skill\n请先阅读说明。\n",
        encoding="utf-8",
    )
    registry = SkillRegistry(get_workspace().skills_dir)
    registry.discover()

    result = run_load_skill(name="demo-skill", registry=registry)

    assert result["title"] == "Loaded skill: demo-skill"
    assert result["metadata"]["status"] == "completed"
    assert result["metadata"]["name"] == "demo-skill"
    assert result["metadata"]["dir"] == str(skill_dir.resolve())
    assert result["output"].startswith("## Skill: demo-skill\nBase directory: ")
    assert "# Demo Skill" in result["output"]


def test_run_load_skill_should_reject_empty_name(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_AGENT_HOME", str(tmp_path / ".my-agent"))
    configure_workspace(tmp_path / "workspace")
    _set_test_session("skills-load-empty")
    registry = SkillRegistry(get_workspace().skills_dir)
    registry.skills = []

    result = run_load_skill(name="   ", registry=registry)

    assert result["metadata"]["status"] == "failed"
    assert result["metadata"]["error_code"] == "skill_name_invalid"


def test_run_load_skill_should_match_name_case_insensitively(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_AGENT_HOME", str(tmp_path / ".my-agent"))
    configure_workspace(tmp_path / "workspace")
    _set_test_session("skills-load-case")
    skill_dir = get_workspace().skills_dir / "demo-skill"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: Demo-Skill\ndescription: demo\n---\n# Demo Skill\n",
        encoding="utf-8",
    )
    registry = SkillRegistry(get_workspace().skills_dir)
    registry.discover()

    result = run_load_skill(name="demo-skill", registry=registry)

    assert result["metadata"]["status"] == "completed"
    assert result["metadata"]["name"] == "Demo-Skill"


def test_run_load_skill_should_return_not_found_when_skill_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_AGENT_HOME", str(tmp_path / ".my-agent"))
    configure_workspace(tmp_path / "workspace")
    _set_test_session("skills-load-missing")
    registry = SkillRegistry(get_workspace().skills_dir)
    registry.skills = []

    result = run_load_skill(name="missing-skill", registry=registry)

    assert result["metadata"]["status"] == "failed"
    assert result["metadata"]["error_code"] == "skill_not_found"
    assert result["metadata"]["name"] == "missing-skill"


def test_resolve_readable_file_path_should_allow_workspace_absolute_path(tmp_path):
    file_path = tmp_path / "sample.txt"
    file_path.write_text("hello", encoding="utf-8")
    configure_workspace(tmp_path)
    _set_test_session()

    assert resolve_readable_file_path(str(file_path)) == file_path.resolve()


def test_run_write_should_return_structured_success(monkeypatch, tmp_path):
    configure_workspace(tmp_path)
    _set_test_session()
    target = (tmp_path / "notes.txt").resolve()

    result = run_write(str(target), "hello")

    assert result["metadata"]["status"] == "completed"
    assert result["success"] is True
    assert result["title"] == "notes.txt"
    assert result["filePath"] == str(target)
    assert result["bytesWritten"] == len("hello".encode("utf-8"))
    assert result["metadata"]["filepath"] == str(target)
    assert result["metadata"]["exists"] is False
    assert result["metadata"]["diagnostics"] == []
    assert result["metadata"]["diagnostics_status"] == "unsupported_language"
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "hello"


def test_run_write_should_allow_runtime_skills_file_creation(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_AGENT_HOME", str(tmp_path / ".my-agent"))
    configure_workspace(tmp_path / "workspace")
    _set_test_session("skills-write")
    target = get_workspace().skills_dir / "demo-skill" / "SKILL.md"
    assert not target.exists()

    result = run_write(str(target.resolve()), "new")

    assert result["metadata"]["status"] == "completed"
    assert target.read_text(encoding="utf-8") == "new"


def test_run_write_should_reject_relative_path(tmp_path):
    configure_workspace(tmp_path)
    _set_test_session()

    result = run_write("notes.txt", "new")

    assert result["metadata"]["status"] == "failed"
    assert result["metadata"]["error_code"] == "write_path_not_absolute"


def test_run_write_should_reject_existing_file_and_guide_to_edit(tmp_path):
    file_path = tmp_path / "notes.txt"
    file_path.write_text("old", encoding="utf-8")
    configure_workspace(tmp_path)
    _set_test_session()

    result = run_write(str(file_path.resolve()), "new")

    assert result["metadata"]["status"] == "failed"
    assert result["metadata"]["error_code"] == "write_file_exists"
    assert "edit_file" in result["output"]


def test_run_write_should_auto_create_parent_directories(tmp_path):
    configure_workspace(tmp_path)
    _set_test_session()
    file_path = (tmp_path / "nested" / "dir" / "notes.txt").resolve()

    result = run_write(str(file_path), "new")

    assert result["metadata"]["status"] == "completed"
    assert file_path.read_text(encoding="utf-8") == "new"


def test_run_write_should_reject_directory_target(tmp_path):
    configure_workspace(tmp_path)
    _set_test_session()
    (tmp_path / "dir").mkdir()

    result = run_write(str((tmp_path / "dir").resolve()), "new")

    assert result["metadata"]["status"] == "failed"
    assert result["metadata"]["error_code"] == "write_path_is_directory"


def test_run_edit_should_require_read_before_edit(tmp_path):
    file_path = tmp_path / "sample.txt"
    file_path.write_text("hello", encoding="utf-8")
    configure_workspace(tmp_path)
    _set_test_session()

    result = run_edit(str(file_path.resolve()), "hello", "world")

    assert result["metadata"]["status"] == "failed"
    assert result["metadata"]["error_code"] == "edit_read_required"


def test_run_edit_should_return_text_not_found_failure(tmp_path):
    file_path = tmp_path / "sample.txt"
    file_path.write_text("hello", encoding="utf-8")
    configure_workspace(tmp_path)
    _set_test_session()
    run_read(str(file_path.resolve()))

    result = run_edit(str(file_path.resolve()), "missing", "world")

    assert result["metadata"]["status"] == "failed"
    assert result["metadata"]["error_code"] == "edit_text_not_found"
    assert "未在" in result["output"]
    assert "空白字符" in result["output"]


def test_run_edit_should_succeed_after_read_and_return_diff(tmp_path):
    file_path = tmp_path / "sample.txt"
    file_path.write_text("hello\nworld\n", encoding="utf-8")
    configure_workspace(tmp_path)
    _set_test_session()
    run_read(str(file_path.resolve()))

    result = run_edit(str(file_path.resolve()), "world", "agent")

    assert result["metadata"]["status"] == "completed"
    assert result["success"] is True
    assert result["operation"] == "replace"
    assert result["replacedCount"] == 1
    assert result["metadata"]["diagnostics"] == []
    assert result["metadata"]["diagnostics_status"] == "unsupported_language"
    assert result["metadata"]["filediff"]["before"] == "hello\nworld\n"
    assert result["metadata"]["filediff"]["after"] == "hello\nagent\n"
    assert "+agent" in result["metadata"]["diff"]
    assert file_path.read_text(encoding="utf-8") == "hello\nagent\n"


def test_run_write_should_append_lsp_errors_into_output(monkeypatch, tmp_path):
    configure_workspace(tmp_path)
    _set_test_session()

    diagnostic = LspDiagnostic(
        severity="error",
        code="E001",
        message="cannot find symbol",
        source="jdtls",
        range=LspRange(
            start=LspPosition(line=11, character=7),
            end=LspPosition(line=11, character=10),
        ),
    )
    monkeypatch.setattr(
        "agent.tools.write_file_tool.collect_file_diagnostics",
        lambda **_: LspDiagnosticsResult(
            status="completed",
            diagnostics=(diagnostic,),
            diagnostics_total=1,
            diagnostics_summary="1 个error",
            lsp_language="java",
            lsp_server="jdtls",
            output_excerpt=(
                "\nLSP 检测到当前文件存在错误，请继续修复：\n"
                f"<diagnostics file=\"{(tmp_path / 'Foo.java').resolve()}\">\n"
                "ERROR [12:8] cannot find symbol\n"
                "</diagnostics>"
            ),
        ),
    )

    result = run_write(str((tmp_path / "Foo.java").resolve()), "class Foo {}")

    assert result["metadata"]["status"] == "completed"
    assert result["metadata"]["diagnostics_status"] == "completed"
    assert result["metadata"]["raw_diagnostics_total"] == 0
    assert result["metadata"]["diagnostics_settled"] is False
    assert "LSP 状态：completed" in result["output"]
    assert "摘要：当前文件存在 1 个error，请继续修复当前文件。" in result["output"]
    assert "LSP 检测到当前文件存在错误" in result["output"]
    assert "ERROR [12:8] cannot find symbol" in result["output"]


def test_lsp_result_metadata_should_include_wait_fields():
    diagnostic = LspDiagnostic(
        severity="error",
        code="E001",
        message="cannot find symbol",
        source="jdtls",
        range=LspRange(
            start=LspPosition(line=1, character=1),
            end=LspPosition(line=1, character=2),
        ),
    )

    metadata = LspDiagnosticsResult(
        status="completed",
        diagnostics=(diagnostic,),
        diagnostics_total=1,
        raw_diagnostics_total=3,
        diagnostics_sequence=2,
        diagnostics_previous_sequence=1,
        diagnostics_latest_sequence=2,
        diagnostics_wait_rounds=1,
        diagnostics_wait_ms=830,
        diagnostics_settled=True,
        lsp_workspace_root="/tmp/project",
        lsp_data_dir="/tmp/.my-agent/lsp/java/abc123",
        lsp_workspace_selection_reason="maven_aggregator_root",
        lsp_server_key="java:/tmp/project:direct_lsp",
        lsp_snapshot_uri="file:///tmp/project/src/Foo.java",
        recent_status_summary="Starting:Init...",
        recent_log_summary="1:build running",
        recent_publish_uris="src/Foo.java#2(3)",
        received_other_file_diagnostics=False,
    ).to_metadata()

    assert metadata["raw_diagnostics_total"] == 3
    assert metadata["diagnostics_sequence"] == 2
    assert metadata["diagnostics_previous_sequence"] == 1
    assert metadata["diagnostics_latest_sequence"] == 2
    assert metadata["diagnostics_wait_rounds"] == 1
    assert metadata["diagnostics_wait_ms"] == 830
    assert metadata["diagnostics_settled"] is True
    assert metadata["lsp_workspace_root"] == "/tmp/project"
    assert metadata["lsp_data_dir"] == "/tmp/.my-agent/lsp/java/abc123"
    assert metadata["lsp_workspace_selection_reason"] == "maven_aggregator_root"
    assert metadata["recent_publish_uris"] == "src/Foo.java#2(3)"


def test_lsp_result_metadata_should_include_debug_observation_fields():
    metadata = LspDiagnosticsResult(
        status="timeout_degraded",
        java_debug_observation_enabled=True,
        debug_status_events="1:Starting:Refreshing '/instruction-service/src/main/java'.",
        debug_log_events="2:1:Error in Java Model (code 969)",
        debug_publish_events="3:src/Foo.java#1(0)",
        debug_issue_probe="contains_code_969=True",
    ).to_metadata()

    assert metadata["java_debug_observation_enabled"] is True
    assert "Refreshing" in metadata["debug_status_events"]
    assert "code 969" in metadata["debug_log_events"]
    assert metadata["debug_publish_events"] == "3:src/Foo.java#1(0)"
    assert metadata["debug_issue_probe"] == "contains_code_969=True"


def test_run_edit_should_mark_server_unavailable_but_keep_success(monkeypatch, tmp_path):
    file_path = tmp_path / "Foo.java"
    file_path.write_text("class Foo {}", encoding="utf-8")
    configure_workspace(tmp_path)
    _set_test_session()
    run_read(str(file_path.resolve()))
    monkeypatch.setattr(
        "agent.tools.edit_file_tool.collect_file_diagnostics",
        lambda **_: LspDiagnosticsResult(
            status="server_unavailable",
            lsp_language="java",
            lsp_server="jdtls",
            lsp_error="jdtls 未安装",
        ),
    )

    result = run_edit(str(file_path.resolve()), "Foo", "Bar")

    assert result["metadata"]["status"] == "completed"
    assert result["metadata"]["diagnostics_status"] == "server_unavailable"
    assert result["metadata"]["lsp_error"] == "jdtls 未安装"
    assert "LSP 状态：server_unavailable" in result["output"]
    assert "摘要：LSP 当前不可用，本次无法提供 diagnostics。" in result["output"]
    assert "原因：jdtls 未安装" in result["output"]


def test_run_write_should_mark_timeout_degraded_but_keep_success(monkeypatch, tmp_path):
    configure_workspace(tmp_path)
    _set_test_session()
    monkeypatch.setattr(
        "agent.tools.write_file_tool.collect_file_diagnostics",
        lambda **_: LspDiagnosticsResult(
            status="timeout_degraded",
            lsp_language="java",
            lsp_server="jdtls",
            lsp_workspace_root=str(tmp_path.resolve()),
            lsp_workspace_selection_reason="maven_aggregator_root",
            lsp_server_key=f"java:{tmp_path.resolve()}:direct_lsp",
            lsp_snapshot_uri=(tmp_path / "Foo.java").resolve().as_uri(),
            recent_status_summary="Starting:Init...",
            recent_log_summary="1:still indexing",
            recent_publish_uris="src/Bar.java#1(2)",
            received_other_file_diagnostics=True,
            lsp_error="等待 diagnostics 超时；已补发 didSave 重试，但仍未收到 publishDiagnostics。",
        ),
    )

    result = run_write(str((tmp_path / "Foo.java").resolve()), "class Foo {}")

    assert result["metadata"]["status"] == "completed"
    assert result["metadata"]["diagnostics_status"] == "timeout_degraded"
    assert "LSP 状态：timeout_degraded" in result["output"]
    assert "摘要：本次 diagnostics 未完整返回，请谨慎依赖本次结果。" in result["output"]
    assert "原因：等待 diagnostics 超时；已补发 didSave 重试，但仍未收到 publishDiagnostics。" in result["output"]
    assert "didSave" in result["output"]
    assert "workspace_selection_reason=maven_aggregator_root" in result["output"]
    assert "recent_publish=src/Bar.java#1(2)" in result["output"]
    assert "LSP 观测信息" not in result["output"]
    assert "workspace_root=" not in result["output"]


def test_run_write_should_surface_project_import_failed_reason(monkeypatch, tmp_path):
    configure_workspace(tmp_path)
    _set_test_session()
    monkeypatch.setattr(
        "agent.tools.write_file_tool.collect_file_diagnostics",
        lambda **_: LspDiagnosticsResult(
            status="project_import_failed",
            lsp_language="java",
            lsp_server="jdtls",
            java_project_issue_code="maven_profile_conflict",
            java_project_state="profile_conflict",
            java_maven_profiles=("hna",),
            java_maven_profiles_source="auto_detected",
            lsp_error="Java 工程导入存在 Maven profile 冲突：当前文件 src/main/java/com/huoli/flight/channel/hna/nineh/b2c/Air9hB2cConvertUtil.java 会被默认激活的 profile airchina 排除；自动探测建议的 profile 为 hna，如仍失败请调整项目目录结构或补充自动探测规则",
        ),
    )

    result = run_write(str((tmp_path / "Foo.java").resolve()), "class Foo {}")

    assert result["metadata"]["status"] == "completed"
    assert result["metadata"]["diagnostics_status"] == "project_import_failed"
    assert result["metadata"]["java_project_issue_code"] == "maven_profile_conflict"
    assert result["metadata"]["java_project_state"] == "profile_conflict"
    assert result["metadata"]["java_maven_profiles"] == ["hna"]
    assert result["metadata"]["java_maven_profiles_source"] == "auto_detected"
    assert "LSP 状态：project_import_failed" in result["output"]
    assert "摘要：工程导入失败，当前 diagnostics 不可用，请先修复工程配置问题。" in result["output"]
    assert "原因：Java 工程导入存在 Maven profile 冲突" in result["output"]
    assert "工程问题标记：maven_profile_conflict（profile_conflict）" in result["output"]
    assert "Maven profile 冲突" in result["output"]
    assert "自动探测建议的 profile 为 hna" in result["output"]


def test_run_write_should_summarize_completed_without_error_diagnostics(monkeypatch, tmp_path):
    configure_workspace(tmp_path)
    _set_test_session()
    monkeypatch.setattr(
        "agent.tools.write_file_tool.collect_file_diagnostics",
        lambda **_: LspDiagnosticsResult(
            status="completed",
            diagnostics=(),
            diagnostics_total=0,
            diagnostics_summary="",
            lsp_language="python",
            lsp_server="pylsp",
        ),
    )

    result = run_write(str((tmp_path / "demo.py").resolve()), "print('ok')")

    assert result["metadata"]["status"] == "completed"
    assert result["metadata"]["diagnostics_status"] == "completed"
    assert "LSP 状态：completed" in result["output"]
    assert "摘要：当前文件未发现 error 级别 diagnostics。" in result["output"]


def test_run_edit_should_fail_when_file_changed_after_read(tmp_path):
    file_path = tmp_path / "sample.txt"
    file_path.write_text("hello", encoding="utf-8")
    configure_workspace(tmp_path)
    _set_test_session()
    run_read(str(file_path.resolve()))
    file_path.write_text("hello2", encoding="utf-8")

    result = run_edit(str(file_path.resolve()), "hello", "world")

    assert result["metadata"]["status"] == "failed"
    assert result["metadata"]["error_code"] == "edit_stale_read"


def test_run_edit_should_fail_when_editing_twice_without_reread(tmp_path):
    file_path = tmp_path / "sample.txt"
    file_path.write_text("hello", encoding="utf-8")
    configure_workspace(tmp_path)
    _set_test_session()
    run_read(str(file_path.resolve()))

    first_result = run_edit(str(file_path.resolve()), "hello", "world")
    second_result = run_edit(str(file_path.resolve()), "world", "agent")

    assert first_result["metadata"]["status"] == "completed"
    assert second_result["metadata"]["status"] == "failed"
    assert second_result["metadata"]["error_code"] == "edit_stale_read"


def test_run_edit_should_support_replace_all(tmp_path):
    file_path = tmp_path / "sample.txt"
    file_path.write_text("name = old\nprint('old')\n", encoding="utf-8")
    configure_workspace(tmp_path)
    _set_test_session()
    run_read(str(file_path.resolve()))

    result = run_edit(str(file_path.resolve()), "old", "new", replace_all=True)

    assert result["metadata"]["status"] == "completed"
    assert result["replacedCount"] == 2
    assert file_path.read_text(encoding="utf-8") == "name = new\nprint('new')\n"


def test_run_edit_should_fail_when_match_is_not_unique(tmp_path):
    file_path = tmp_path / "sample.txt"
    file_path.write_text("old\nold\n", encoding="utf-8")
    configure_workspace(tmp_path)
    _set_test_session()
    run_read(str(file_path.resolve()))

    result = run_edit(str(file_path.resolve()), "old", "new")

    assert result["metadata"]["status"] == "failed"
    assert result["metadata"]["error_code"] == "edit_match_not_unique"
    assert "1-2 行上下文" in result["output"]


def test_run_edit_should_append_when_old_string_is_empty(tmp_path):
    configure_workspace(tmp_path)
    _set_test_session()
    existing = tmp_path / "existing.txt"
    existing.write_text("old\n", encoding="utf-8")
    run_read(str(existing.resolve()))

    append_result = run_edit(str(existing.resolve()), "", "new content")

    assert append_result["metadata"]["status"] == "completed"
    assert append_result["operation"] == "append"
    assert append_result["replacedCount"] == 1
    assert existing.read_text(encoding="utf-8") == "old\nnew content"


def test_run_edit_should_delete_when_new_string_is_empty(tmp_path):
    file_path = tmp_path / "sample.txt"
    file_path.write_text("hello\nworld\n", encoding="utf-8")
    configure_workspace(tmp_path)
    _set_test_session()
    run_read(str(file_path.resolve()))

    result = run_edit(str(file_path.resolve()), "world\n", "")

    assert result["metadata"]["status"] == "completed"
    assert result["operation"] == "delete"
    assert result["replacedCount"] == 1
    assert file_path.read_text(encoding="utf-8") == "hello\n"


def test_run_edit_should_fail_when_file_missing_and_guide_to_write(tmp_path):
    configure_workspace(tmp_path)
    _set_test_session()
    file_path = (tmp_path / "missing.txt").resolve()

    result = run_edit(str(file_path), "old", "new")

    assert result["metadata"]["status"] == "failed"
    assert result["metadata"]["error_code"] == "edit_file_missing"
    assert "write_file" in result["output"]


def test_run_edit_should_reject_relative_path(tmp_path):
    file_path = tmp_path / "sample.txt"
    file_path.write_text("hello", encoding="utf-8")
    configure_workspace(tmp_path)
    _set_test_session()

    result = run_edit("sample.txt", "hello", "world")

    assert result["metadata"]["status"] == "failed"
    assert result["metadata"]["error_code"] == "edit_path_not_absolute"


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
