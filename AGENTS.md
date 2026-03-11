# Repository Guidelines

## 规范优先级（重要）
- 开发实现必须优先遵循 `README.md` 中的开发规范与分层约束。
- 若 `AGENTS.md` 与 `README.md` 出现不一致，以 `README.md` 为准，并同步更新本文件。
- 提交前应自检：实现位置、职责边界、测试策略是否与 `README.md` 保持一致。

## Project Structure & Module Organization
- `src/main.py`: lightweight runnable entrypoint for a demo session.
- `src/agent/runtime/session.py`: session orchestration and tool-call loop.
- `src/agent/runtime/main_agent_mode.py`: main-agent mode state (`build`/`plan`) management.
- `src/agent/adapters/llm/client.py`: provider client adapter and LLM hooks.
- `src/agent/runtime/tool_executor.py`: shared tool execution and tool hook dispatch.
- `src/agent/tools/`: tool specs, handlers, and todo manager.
- `src/agent/core/`: core message/context models and generic hook dispatcher.
- `.vscode/`: local editor settings only; avoid putting runtime logic here.
- Keep new Python modules under `src/agent/` and group by responsibility.

## 分层职责约束（与 README 对齐）
- `runtime/session.py` 只做会话编排（消息循环、模式选择、工具分发），不放具体工具业务逻辑。
- 工具实现统一放在 `tools/handlers.py`，工具协议统一放在 `tools/specs.py`。
- 主 agent 模式状态统一放在 `runtime/main_agent_mode.py`，禁止在其他模块散落存储。
- 多主/子 agent 扩展统一遵循：
  - 主 agent：`build` / `plan`，通过 `plan_enter` / `plan_exit` 切换。
  - 子 agent：统一走 `task`，通过 `agent` 参数路由（当前支持 `explore`）。
  - `plan` 模式安全限制：写入仅限 `src/plan/`，`bash` 仅允许只读命令。

## Build, Test, and Development Commands
- `python3 src/main.py`: run the current demo flow end-to-end.
- `python3 -m py_compile $(find src -name '*.py')`: quick syntax validation before commit.
- `python3 -m venv .venv && source .venv/bin/activate`: create and activate an isolated environment.
- If you add dependencies, document install commands in this file and pin versions.

## Coding Style & Naming Conventions
- Follow PEP 8 with 4-space indentation and clear, small functions.
- Use `snake_case` for variables/functions, `UPPER_CASE` for constants, and `PascalCase` for classes.
- Prefer type hints on public functions (e.g., `def run_read(path: str) -> str`).
- Keep side effects explicit; separate pure logic from subprocess/file operations.

## Testing Guidelines
- Current repository has no formal test suite; add `pytest` tests under `tests/`.
- Name files as `test_<module>.py` and test functions as `test_<behavior>()`.
- For tool safety logic, include edge-case tests (path traversal, blocked commands, timeout handling).
- Run tests with `pytest -q` once tests are introduced.
- 新增或调整工具时，至少覆盖：
  - 工具函数单测（`tests/test_handlers.py` 维度）
  - 会话编排集成测试（`tests/test_run_session.py` 维度）

## Commit & Pull Request Guidelines
- No Git history is available in this workspace, so use a consistent convention now:
  - Commit format: `feat: ...`, `fix: ...`, `refactor: ...`, `test: ...`, `docs: ...`.
  - Example: `fix: block dangerous shell patterns in run_bash`.
- PRs should include: purpose, key changes, verification steps, and risk/security impact.

## Security & Configuration Tips
- Never hardcode secrets (API keys, tokens). Use environment variables instead.
- Validate all filesystem paths with workspace boundaries (as `safe_path` does).
- Treat shell execution as high risk; keep deny-lists and timeouts, and prefer allow-listing when expanding capabilities.
