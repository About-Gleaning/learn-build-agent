# Repository Guidelines

## Project Structure & Module Organization
- `src/main.py`: entry point for the agent loop and model client setup.
- `src/tool.py`: local tool handlers (`bash`, file read/write/edit) and workspace safety checks.
- `.vscode/`: local editor settings only; avoid putting runtime logic here.
- Keep new Python modules under `src/` and group by responsibility (for example, `src/tools/`, `src/core/`).

## Build, Test, and Development Commands
- `python3 src/main.py`: run the current demo flow end-to-end.
- `python3 -m py_compile src/*.py`: quick syntax validation before commit.
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
