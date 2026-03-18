from agent.tools.bash_tool import validate_readonly_bash
from agent.tools.handlers import (
    build_plan_placeholder_path,
    build_tool_failure,
    build_tool_success,
    is_allowed_plan_write_path,
    run_edit,
    run_plan_enter,
    run_plan_exit,
    run_read,
    run_write,
)
from agent.tools.todo_manager import TodoManager


def test_build_plan_placeholder_path_should_be_absolute():
    path = build_plan_placeholder_path("s:1/test")
    assert path.is_absolute()
    assert path.name == "s_1_test.md"


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


def test_is_allowed_plan_write_path():
    assert is_allowed_plan_write_path("src/storage/plan/a.md")
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
    monkeypatch.setattr("agent.tools.handlers.WORKDIR", tmp_path)

    result = run_read("sample.txt", limit=2, offset=1)

    assert result["metadata"]["status"] == "completed"
    assert result["output"] == "b\nc\n... (1 more lines)"


def test_run_write_should_return_structured_success(monkeypatch, tmp_path):
    monkeypatch.setattr("agent.tools.handlers.WORKDIR", tmp_path)

    result = run_write("notes.txt", "hello")

    assert result["metadata"]["status"] == "completed"
    assert result["output"] == "Wrote 5 bytes to notes.txt"
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "hello"


def test_run_edit_should_return_text_not_found_failure(monkeypatch, tmp_path):
    file_path = tmp_path / "sample.txt"
    file_path.write_text("hello", encoding="utf-8")
    monkeypatch.setattr("agent.tools.handlers.WORKDIR", tmp_path)

    result = run_edit("sample.txt", "missing", "world")

    assert result["metadata"]["status"] == "failed"
    assert result["metadata"]["error_code"] == "text_not_found"
    assert "Text not found" in result["output"]


def test_build_plan_placeholder_path_should_anchor_to_project_root(monkeypatch, tmp_path):
    project_root = tmp_path / "project-root"
    project_root.mkdir()
    monkeypatch.setattr("agent.tools.handlers.WORKDIR", project_root)
    monkeypatch.setattr("agent.tools.handlers.PLAN_WRITE_ROOT", (project_root / "src" / "storage" / "plan").resolve())

    path = build_plan_placeholder_path("session:plan")

    assert path == (project_root / "src" / "storage" / "plan" / "session_plan.md").resolve()


def test_todo_manager_should_anchor_relative_storage_dir_to_project_root(monkeypatch, tmp_path):
    project_root = tmp_path / "project-root"
    outside_dir = tmp_path / "outside"
    project_root.mkdir()
    outside_dir.mkdir()
    monkeypatch.setattr("agent.tools.todo_manager.PROJECT_ROOT", project_root)

    manager = TodoManager()

    assert manager.storage_dir == (project_root / "src" / "storage" / "todo").resolve()
    assert manager.storage_dir != (outside_dir / "src" / "storage" / "todo").resolve()
