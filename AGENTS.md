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
- `src/agent/runtime/stream_display.py`：流式事件、`process_items`、`display_parts` 与响应摘要组装。
- `src/agent/adapters/llm/client.py`：LLM 适配与 LLM Hook。
- `src/agent/config/logging_setup.py`：统一日志初始化、格式规范与日志脱敏。
- `src/agent/config/project_runtime.json`：项目级运行时配置（如压缩开关、保留策略）。
- `src/agent/tools/`：工具实现（`handlers.py`）与协议定义（`specs.py`）。
- `src/agent/tools/bash_tool.py`：bash 工具执行与 Plan 模式只读校验。
- `src/agent/tools/task.txt`：`task` 工具描述模板，使用 `{agents}` 占位注入 subagent 列表。
- `src/agent/core/`：消息模型、上下文与通用 HookDispatcher。
- `src/agent/web/`：Web API 与请求/响应模型。
- `src/agent/web/serializers.py`：`Message -> MessageVO` 与 SSE payload 序列化。
- `tests/`：`pytest` 用例（工具、会话、Web、Hook 等）。

## 分层职责约束（与 README 对齐）

- `runtime/session.py` 只做会话编排，不放具体工具业务逻辑；流式事件、`process_items`、`display_parts` 与响应摘要拼装统一放在 `runtime/stream_display.py`。
- `runtime/agents.py` 是 agent 元信息唯一来源；每个 agent 必须声明 `model`（`primary`/`subagent`）和 `description`。
- 工具实现统一在 `tools/` 目录内分模块维护；bash 相关逻辑放 `tools/bash_tool.py`，其余通用工具默认放 `tools/handlers.py`，工具协议统一在 `tools/specs.py`。
- 文件工具与 plan 模式拦截默认返回结构化结果，至少包含 `output` 与 `metadata.status`；失败场景应补充 `metadata.error_code`。
- `plan_enter` / `plan_exit` 只允许发起切换申请，确认与取消必须由程序侧状态机控制，禁止继续通过 LLM 参数决定。
- Web 端“确认切换”必须通过流式接口继续执行确认后的会话，避免阻塞式请求导致界面无法实时更新。
- skills 的可用目录统一通过 `load_skill` 工具描述动态暴露，禁止继续在 agent prompt 中注入 `skills_catalog`。
- 主 Agent 模式状态统一收敛在 `runtime/main_agent_mode.py`（若启用该模块），禁止散落存储。
- 子 Agent 扩展统一通过 `task` 工具路由，不在会话层写业务分支。
- `task` 工具中的 subagent 名单与说明，必须从 `runtime/agents.py` 动态生成，禁止在 `specs.py` 或 `session.py` 中手写支持列表。
- Web 时间线必须按 `session` 维度累计展示，禁止在前端新一轮提交时覆盖上一轮执行轨迹。
- `task` 委派 subagent 时，流式事件必须透传 subagent 内部进度，并使用后端生成的 `delegation_id` 作为稳定关联键。
- Web 助手消息展示必须优先基于后端返回的 `display_parts` 顺序片段流，确保 `assistant_text` 与 `tool_call`/`tool_result` 按真实发生顺序穿插；仅在旧消息缺少该字段时才回退到 `process_items + text` 的兼容渲染。
- Web 序列化逻辑统一收敛在 `web/serializers.py`，禁止在 `web/app.py` 中继续大段手工映射 `MessageVO` 或 SSE payload。
- 日志必须通过程序显式传递 `agent`、`model` 等上下文字段，禁止依赖 LLM 生成或推断日志元信息。
- 业务正常链路日志仅保留 LLM 调用前后、工具调用前后；其余调试日志默认不落盘。
- 日志文件统一写入 `logs/app-YYYY-MM-dd.log`，并使用追加模式保留历史内容。
- `build` 主模式的提示词文件必须按厂商 `vendor` 选择，命名统一为 `build.<vendor>.txt`；厂商归属在 `src/agent/config/llm_runtime.json` 中显式声明，缺省时回退 `build.default.txt`。
- 项目级运行时开关统一放在 `src/agent/config/project_runtime.json`，禁止继续在 `runtime/compaction.py` 等业务模块中硬编码可配置策略。
- `project_runtime.json` 中的 `compaction` 必须采用 `default + vendors` 结构；命中当前模型厂商 `vendor` 时，仅覆盖显式配置字段，未配置字段继续继承 `default`。
- `compaction.tool_result_keep_recent` 的计数口径统一按 `role=tool` 消息数量计算，默认保留最近 `3` 条不压缩。
- 当前可配置的压缩关键参数包括：`tool_result_prune_enabled`、`tool_result_keep_recent`、`tool_result_prune_min_chars`、`summary_trigger_threshold`、`summary_max_tokens`、`tool_output_max_lines`、`tool_output_max_bytes`。

## 开发与验证命令

- `python3 src/main.py`：运行 CLI 示例流程。
- `pytest -q`：执行测试。
- `PYTHONPYCACHEPREFIX=/tmp python3 -m py_compile $(find src -name '*.py')`：语法检查。
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
- LLM 调用必须配置显式超时；当主代理在 `task` 委派后继续二轮推理发生超时时，必须记录错误日志并返回可解释失败结果，禁止无限等待。

## 变更记录

- 2026-03-18：完成第一阶段运行时重构，收敛 `session.py` 中的会话初始化、`task` 工具参数解析与模式切换结果处理重复逻辑，并补充非法 `task` 参数回归测试。
- 2026-03-18：完成第二阶段运行时重构，将流式事件、`process_items`、`display_parts` 与响应摘要汇总逻辑抽离到独立的 `runtime/stream_display.py`，降低 `session.py` 与展示层的耦合。
- 2026-03-18：完成第三阶段工具层重构，统一文件工具与 plan 模式拦截的结构化返回，补充 `handlers` 成功/失败结果与错误码回归测试，并保持 tool 输出文本兼容原行为。
- 2026-03-18：完成第四阶段 Web 重构，将 `Message -> VO` 与 SSE 事件序列化抽离到 `web/serializers.py`，降低 `app.py` 中的手工字段映射与重复流式封装逻辑。
- 2026-03-18：完成第五阶段收口整理，清理 `session.py` 中的未使用局部变量与 `handlers.py` 的无用日志对象，并为工具结果构造与 Web 序列化补充关键注释。
