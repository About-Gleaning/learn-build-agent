# Repository Guidelines

## 规范优先级（重要）

- 开发实现必须优先遵循 `README.md` 中的开发规范与分层约束。
- 若 `AGENTS.md` 与 `README.md` 不一致，以 `README.md` 为准，并同步更新本文件。
- 提交前必须自检：实现位置、职责边界、测试策略、安全约束是否与 `README.md` 一致。

## 项目结构与模块分工

- `src/main.py`：CLI 示例入口。
- `src/web_main.py`：FastAPI 启动入口（`uvicorn src.web_main:app`）。
- `src/agent/runtime/agents.py`：Agent 元信息注册与 subagent 管理。
- `src/agent/runtime/session.py`：会话编排（消息循环、模式切换、工具分发）。
- `src/agent/runtime/session_memory.py`：会话记忆与状态持久化辅助。
- `src/agent/runtime/tool_executor.py`：工具执行器与 Tool Hook 分发。
- `src/agent/runtime/compaction.py`：上下文压缩逻辑。
- `src/agent/adapters/llm/client.py`：LLM 适配与 LLM Hook。
- `src/agent/tools/`：工具实现（`handlers.py`）与协议定义（`specs.py`）。
- `src/agent/tools/task.txt`：`task` 工具描述模板，使用 `{agents}` 占位注入 subagent 列表。
- `src/agent/core/`：消息模型、上下文与通用 HookDispatcher。
- `src/agent/web/`：Web API 与请求/响应模型。
- `tests/`：`pytest` 用例（工具、会话、Web、Hook 等）。

## 分层职责约束（与 README 对齐）

- `runtime/session.py` 只做会话编排，不放具体工具业务逻辑。
- `runtime/agents.py` 是 agent 元信息唯一来源；每个 agent 必须声明 `model`（`primary`/`subagent`）和 `description`。
- 工具实现统一在 `tools/handlers.py`，工具协议统一在 `tools/specs.py`。
- skills 的可用目录统一通过 `load_skill` 工具描述动态暴露，禁止继续在 agent prompt 中注入 `skills_catalog`。
- 主 Agent 模式状态统一收敛在 `runtime/main_agent_mode.py`（若启用该模块），禁止散落存储。
- 子 Agent 扩展统一通过 `task` 工具路由，不在会话层写业务分支。
- `task` 工具中的 subagent 名单与说明，必须从 `runtime/agents.py` 动态生成，禁止在 `specs.py` 或 `session.py` 中手写支持列表。

## 开发与验证命令

- `python3 src/main.py`：运行 CLI 示例流程。
- `pytest -q`：执行测试。
- `python3 -m py_compile $(find src -name '*.py')`：语法检查。
- `python3 -m venv .venv && source .venv/bin/activate`：创建并激活虚拟环境。
- 新增依赖时必须在 `requirements.txt` 固定版本，并同步更新 README。

## 代码风格

- 遵循 PEP 8，4 空格缩进，函数职责单一。
- 命名规范：变量/函数 `snake_case`，常量 `UPPER_CASE`，类名 `PascalCase`。
- 公共函数优先补全类型标注。
- 副作用操作（文件、子进程、网络）与纯逻辑分离，便于测试与审计。

## 测试要求

- 统一使用 `pytest`，测试文件命名 `test_<module>.py`。
- 新增或调整工具时至少覆盖：
  - `tests/test_handlers.py` 维度：参数校验、正常路径、异常路径。
  - `tests/test_run_session.py` 维度：会话编排与工具路由集成行为。
- 新增或调整 agent / subagent 时，至少覆盖：
  - `task` 描述是否包含最新 subagent 名称与 description。
  - 非 `subagent` agent 是否被 `task` 正确拒绝。
- 涉及 Web API 时补充 `tests/test_web_api.py` 维度覆盖。
- 安全相关逻辑必须覆盖边界用例（路径穿越、危险命令、超时、权限限制）。

## 提交与评审规范

- 提交类型建议：`feat:`、`fix:`、`refactor:`、`test:`、`docs:`。
- Commit message 与 PR 描述优先中文，确保语义清晰。
- PR 内容至少包含：变更目的、关键改动、验证步骤、风险与安全影响。

## 安全规范

- 禁止硬编码任何密钥或令牌，统一使用环境变量。
- 所有路径输入必须通过工作区边界校验（如 `safe_path`）。
- Shell 执行默认高风险，优先白名单、超时与最小权限策略。
