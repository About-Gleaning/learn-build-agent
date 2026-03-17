from agent.tools.bash_tool import validate_readonly_bash
from agent.tools.handlers import (
    build_plan_placeholder_path,
    is_allowed_plan_write_path,
    run_plan_enter,
    run_plan_exit,
)


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
    assert is_allowed_plan_write_path("src/plan/a.md")
    assert not is_allowed_plan_write_path("src/main.py")
