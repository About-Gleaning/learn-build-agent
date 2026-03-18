import pytest

from agent.config.settings import clear_runtime_settings_cache
from agent.runtime.compaction import TOOL_OUTPUT_MAX_BYTES
from agent.runtime.session import run_session
from agent.core.message import append_text_part, append_tool_call_part, create_message, get_message_text
from agent.runtime.tool_executor import (
    ToolHook,
    ToolLoggingHook,
    ToolExecutor,
    clear_global_tool_hooks,
    register_global_tool_hook,
)
from agent.runtime.workspace import configure_workspace, get_workspace


@pytest.fixture(autouse=True)
def reset_global_tool_hooks():
    clear_global_tool_hooks()
    register_global_tool_hook(ToolLoggingHook())
    yield
    clear_global_tool_hooks()
    register_global_tool_hook(ToolLoggingHook())


class RecorderToolHook(ToolHook):
    def __init__(self, name: str, records: list[str], fail_fast: bool = False) -> None:
        super().__init__(name=name, fail_fast=fail_fast)
        self.records = records

    def before_call(self, ctx):
        self.records.append(f"{self.name}.before")

    def after_call(self, ctx, result):
        self.records.append(f"{self.name}.after")

    def on_error(self, ctx, error, normalized_error):
        self.records.append(f"{self.name}.error:{normalized_error.get('code', '')}")


class BrokenBeforeHook(ToolHook):
    def __init__(self, fail_fast: bool):
        super().__init__(name="broken_before", fail_fast=fail_fast)

    def before_call(self, ctx):
        raise RuntimeError("tool before failed")


class ErrorCodeHook(ToolHook):
    def __init__(self, records: list[str]):
        super().__init__(name="error_capture", fail_fast=False)
        self.records = records

    def on_error(self, ctx, error, normalized_error):
        self.records.append(normalized_error.get("code", ""))


def _mock_chat_with_one_tool_then_text():
    call_state = {"count": 0}

    def _fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        session_id = messages[-1]["info"]["session_id"]
        call_state["count"] += 1
        assistant = create_message("assistant", session_id, status="completed")
        if call_state["count"] == 1:
            append_tool_call_part(
                assistant,
                tool_call_id="call_1",
                name="todo_read",
                arguments="{}",
            )
        else:
            append_text_part(assistant, "done")
        return assistant

    return _fake_chat


def test_tool_hooks_order_global_then_local(monkeypatch):
    import agent.runtime.session as main_module

    records: list[str] = []
    clear_global_tool_hooks()
    register_global_tool_hook(RecorderToolHook("g1", records))
    local_hook = RecorderToolHook("l1", records)

    monkeypatch.setattr(main_module, "create_chat_completion", _mock_chat_with_one_tool_then_text())

    result = run_session("测试", session_id="s_tool", tool_hooks=[local_hook])

    assert get_message_text(result) == "done"
    assert records == ["g1.before", "l1.before", "g1.after", "l1.after"]


def test_tool_hook_fail_open_should_continue(monkeypatch):
    import agent.runtime.session as main_module

    clear_global_tool_hooks()
    register_global_tool_hook(BrokenBeforeHook(fail_fast=False))

    monkeypatch.setattr(main_module, "create_chat_completion", _mock_chat_with_one_tool_then_text())

    result = run_session("测试", session_id="s_open")

    assert get_message_text(result) == "done"


def test_tool_hook_fail_fast_should_interrupt(monkeypatch):
    import agent.runtime.session as main_module

    clear_global_tool_hooks()
    register_global_tool_hook(BrokenBeforeHook(fail_fast=True))

    monkeypatch.setattr(main_module, "create_chat_completion", _mock_chat_with_one_tool_then_text())

    with pytest.raises(RuntimeError, match="Hook 'broken_before' failed"):
        run_session("测试", session_id="s_fast")


def test_tool_error_hook_unknown_tool(monkeypatch):
    import agent.runtime.session as main_module

    records: list[str] = []
    clear_global_tool_hooks()
    register_global_tool_hook(ErrorCodeHook(records))

    call_state = {"count": 0}

    def _fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        session_id = messages[-1]["info"]["session_id"]
        call_state["count"] += 1
        assistant = create_message("assistant", session_id, status="completed")
        if call_state["count"] == 1:
            append_tool_call_part(
                assistant,
                tool_call_id="call_x",
                name="unknown_tool_xyz",
                arguments="{}",
            )
        else:
            append_text_part(assistant, "final")
        return assistant

    monkeypatch.setattr(main_module, "create_chat_completion", _fake_chat)

    result = run_session("测试", session_id="s_err")

    assert get_message_text(result) == "final"
    assert records == ["unknown_tool"]


def test_tool_executor_should_truncate_long_output_and_write_full_file(tmp_path):
    configure_workspace(tmp_path)
    executor = ToolExecutor({"demo_tool": lambda: "x" * (TOOL_OUTPUT_MAX_BYTES + 32)})
    result = executor.execute(
        "demo_tool",
        "{}",
        session_id="s_truncate",
        tool_call_id="call_demo",
        round_no=1,
        hooks=[],
        task_available=False,
        workdir=str(tmp_path),
    )

    metadata = result["metadata"]
    assert metadata["truncated"] is True
    assert "full_output_path" in metadata
    assert "bash + rg" in result["output"]
    full_output_path = (
        get_workspace().tool_output_root
        / get_workspace().workspace_id
        / "s_truncate"
        / "demo_tool-call_demo.log"
    )
    assert full_output_path.exists()
    assert full_output_path.read_text(encoding="utf-8") == "x" * (TOOL_OUTPUT_MAX_BYTES + 32)


def test_tool_executor_should_use_vendor_specific_output_limit(tmp_path, monkeypatch):
    config_path = tmp_path / "project_runtime.json"
    config_path.write_text(
        """
        {
          "compaction": {
            "default": {
              "tool_output_max_bytes": 51200
            },
            "vendors": {
              "qwen": {
                "tool_output_max_bytes": 32
              }
            }
          }
        }
        """.strip(),
        encoding="utf-8",
    )
    clear_runtime_settings_cache()
    monkeypatch.setattr("agent.config.settings.PROJECT_RUNTIME_CONFIG_PATH", config_path)
    executor = ToolExecutor({"demo_tool": lambda: "x" * 128})

    try:
        result = executor.execute(
            "demo_tool",
            "{}",
            session_id="s_truncate_vendor",
            tool_call_id="call_vendor",
            round_no=1,
            hooks=[],
            vendor="qwen",
            task_available=False,
            workdir=str(tmp_path),
        )
    finally:
        clear_runtime_settings_cache()

    metadata = result["metadata"]
    assert metadata["truncated"] is True
    assert metadata["preview_bytes"] <= 32


def test_tool_executor_should_allow_custom_output_processor_override(tmp_path):
    configure_workspace(tmp_path)

    def custom_processor(result, ctx, options):
        del ctx, options
        metadata = dict(result.get("metadata", {}))
        metadata["truncated"] = "custom"
        result["metadata"] = metadata
        result["output"] = "custom-output"
        return result

    executor = ToolExecutor(
        {"demo_tool": lambda: "x" * (TOOL_OUTPUT_MAX_BYTES + 32)},
        output_processors={"demo_tool": custom_processor},
    )

    result = executor.execute(
        "demo_tool",
        "{}",
        session_id="s_override",
        tool_call_id="call_override",
        round_no=1,
        hooks=[],
        task_available=True,
        workdir=str(tmp_path),
    )

    assert result["output"] == "custom-output"
    assert result["metadata"]["truncated"] == "custom"
    assert not (get_workspace().tool_output_root / get_workspace().workspace_id).exists()


def test_tool_logging_hook_should_log_agent_model_args_and_result(caplog):
    executor = ToolExecutor({"demo_tool": lambda value: f"result:{value}"})

    with caplog.at_level("INFO"):
        executor.execute(
            "demo_tool",
            '{"value":"ok"}',
            session_id="s_log",
            tool_call_id="call_log",
            round_no=1,
            hooks=[ToolLoggingHook()],
            agent="build",
            model="demo-model",
            task_available=False,
        )

    assert "tool.request tool=demo_tool args={\"value\":\"ok\"}" in caplog.text
    assert "tool.response tool=demo_tool result=result:ok" in caplog.text
    assert any(record.agent == "build" and record.model == "demo-model" for record in caplog.records)


def test_tool_logging_hook_should_log_truncation_file_path(caplog, tmp_path):
    configure_workspace(tmp_path)
    executor = ToolExecutor({"demo_tool": lambda: "x" * (TOOL_OUTPUT_MAX_BYTES + 32)})
    with caplog.at_level("INFO"):
        executor.execute(
            "demo_tool",
            "{}",
            session_id="s_truncated_log",
            tool_call_id="call_truncated_log",
            round_no=1,
            hooks=[ToolLoggingHook()],
            agent="build",
            model="demo-model",
            task_available=False,
            workdir=str(tmp_path),
        )

    expected_path = (
        get_workspace().tool_output_root
        / get_workspace().workspace_id
        / "s_truncated_log"
        / "demo_tool-call_truncated_log.log"
    )
    assert f"tool.output_truncated tool=demo_tool session_id=s_truncated_log tool_call_id=call_truncated_log" in caplog.text
    assert f"full_output_path={expected_path}" in caplog.text
    assert "write_error=" in caplog.text


def test_tool_logging_hook_should_not_log_truncation_when_output_not_truncated(caplog):
    executor = ToolExecutor({"demo_tool": lambda: "short output"})

    with caplog.at_level("INFO"):
        executor.execute(
            "demo_tool",
            "{}",
            session_id="s_not_truncated_log",
            tool_call_id="call_not_truncated_log",
            round_no=1,
            hooks=[ToolLoggingHook()],
            agent="build",
            model="demo-model",
            task_available=False,
        )

    assert "tool.output_truncated" not in caplog.text


def test_tool_logging_hook_should_log_truncation_write_error(caplog, monkeypatch, tmp_path):
    configure_workspace(tmp_path)
    executor = ToolExecutor({"demo_tool": lambda: "x" * (TOOL_OUTPUT_MAX_BYTES + 32)})

    def _raise_write_error(self, data, encoding=None, errors=None, newline=None):
        del data, encoding, errors, newline
        raise OSError("disk full")

    monkeypatch.setattr("pathlib.Path.write_text", _raise_write_error)

    with caplog.at_level("INFO"):
        executor.execute(
            "demo_tool",
            "{}",
            session_id="s_truncated_error_log",
            tool_call_id="call_truncated_error_log",
            round_no=1,
            hooks=[ToolLoggingHook()],
            agent="build",
            model="demo-model",
            task_available=False,
            workdir=str(tmp_path),
        )

    assert "tool.output_truncated tool=demo_tool session_id=s_truncated_error_log tool_call_id=call_truncated_error_log" in caplog.text
    assert "write_error=OSError: disk full" in caplog.text


def test_tool_executor_should_write_full_file_to_workspace_runtime_when_cwd_differs(tmp_path):
    project_root = tmp_path / "project-root"
    external_workdir = tmp_path / "outside"
    project_root.mkdir()
    external_workdir.mkdir()
    configure_workspace(project_root)
    executor = ToolExecutor({"demo_tool": lambda: "x" * (TOOL_OUTPUT_MAX_BYTES + 32)})
    result = executor.execute(
        "demo_tool",
        "{}",
        session_id="s_fixed_root",
        tool_call_id="call_fixed_root",
        round_no=1,
        hooks=[],
        task_available=False,
        workdir=str(external_workdir),
    )

    output_path = (
        get_workspace().tool_output_root
        / get_workspace().workspace_id
        / "s_fixed_root"
        / "demo_tool-call_fixed_root.log"
    )
    assert result["metadata"]["full_output_path"] == str(output_path.resolve())
    assert output_path.exists()
    assert not (external_workdir / "src" / "storage" / "tool-output").exists()
