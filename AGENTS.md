# Repository Guidelines

## Project Structure & Module Organization
- `src/main.py`: lightweight runnable entrypoint for a demo session.
- `src/agent/runtime/session.py`: session orchestration and tool-call loop.
- `src/agent/adapters/llm/client.py`: provider client adapter and LLM hooks.
- `src/agent/runtime/tool_executor.py`: shared tool execution and tool hook dispatch.
- `src/agent/tools/`: tool specs, handlers, and todo manager.
- `src/agent/core/`: core message/context models and generic hook dispatcher.
- `.vscode/`: local editor settings only; avoid putting runtime logic here.
- Keep new Python modules under `src/agent/` and group by responsibility.

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

## Commit & Pull Request Guidelines
- No Git history is available in this workspace, so use a consistent convention now:
  - Commit format: `feat: ...`, `fix: ...`, `refactor: ...`, `test: ...`, `docs: ...`.
  - Example: `fix: block dangerous shell patterns in run_bash`.
- PRs should include: purpose, key changes, verification steps, and risk/security impact.

## Security & Configuration Tips
- Never hardcode secrets (API keys, tokens). Use environment variables instead.
- Validate all filesystem paths with workspace boundaries (as `safe_path` does).
- Treat shell execution as high risk; keep deny-lists and timeouts, and prefer allow-listing when expanding capabilities.
