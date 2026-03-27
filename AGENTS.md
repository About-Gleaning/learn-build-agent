# Repository Guidelines

## 规范优先级（重要）

- 开发实现必须优先遵循 `README.md` 中的开发规范与分层约束。
- 若 `AGENTS.md` 与 `README.md` 不一致，以 `README.md` 为准，并同步更新本文件。
- 提交前必须自检：实现位置、职责边界、测试策略、安全约束是否与 `README.md` 一致。

## 项目结构与模块分工

- `src/main.py`：兼容 CLI 入口，内部转调 `agent.cli`。
- `src/web_main.py`：FastAPI 启动入口（兼容 `uvicorn src.web_main:app`）。
- `src/agent/cli.py`：正式 CLI 入口，支持 `my-agent` / `my-agent web`。
- `src/agent/runtime/agents.py`：Agent 元信息注册与 subagent 管理。
- `src/agent/runtime/session.py`：会话编排（消息循环、模式切换、工具分发）。
- `src/agent/runtime/session_memory.py`：会话记忆与状态持久化辅助。
- `src/agent/runtime/tool_executor.py`：工具执行器与 Tool Hook 分发。
- `src/agent/runtime/compaction.py`：上下文压缩逻辑。
- `src/agent/runtime/stream_display.py`：流式事件、`process_items`、`display_parts` 与响应摘要组装。
- `src/agent/runtime/workspace.py`：工作区根目录、运行态目录与启动模式解析。
- `src/agent/adapters/llm/client.py`：LLM 统一调用入口与 LLM Hook。
- `src/agent/adapters/llm/protocols.py`：协议层适配（`responses` / `chat_completions`）。
- `src/agent/adapters/llm/vendors.py`：厂商方言注册与独立转换层。
- `src/agent/config/logging_setup.py`：统一日志初始化、格式规范与日志脱敏。
- `src/agent/config/project_runtime.json`：项目级运行时配置（如压缩开关、保留策略）。
- `src/agent/tools/`：工具分模块实现（如 `handlers.py`、`question_tool.py`、`read_file_tool.py`）与协议定义（`specs.py`）。
- `src/agent/tools/bash_tool.py`：bash 工具执行与 Plan 模式只读校验。
- `src/agent/tools/write_file_tool.py`：`write_file` 工具实现与整文件覆盖写入。
- `src/agent/tools/edit_file_tool.py`：`edit_file` 工具实现与精确替换、diff 摘要。
- `src/agent/tools/file_edit_state.py`：当前 session 的文件读取/编辑时序状态。
- `src/agent/tools/grep_tool.py`：grep 工具实现与内容正则搜索。
- `src/agent/tools/glob_tool.py`：glob 工具实现与按修改时间倒序的文件搜索。
- `src/agent/tools/path_utils.py`：工具公共路径解析与工作区目录校验。
- `src/agent/tools/task.txt`：`task` 工具描述模板，使用 `{agents}` 占位注入 subagent 列表。
- `src/agent/core/`：消息模型、上下文与通用 HookDispatcher。
- `src/agent/web/`：Web API 与请求/响应模型。
- `src/agent/web/serializers.py`：`Message -> MessageVO` 与 SSE payload 序列化。
- `tests/`：`pytest` 用例（工具、会话、Web、Hook 等）。

## 分层职责约束（与 README 对齐）

- `runtime/session.py` 只做会话编排，不放具体工具业务逻辑；流式事件、`process_items`、`display_parts` 与响应摘要拼装统一放在 `runtime/stream_display.py`。
- `adapters/llm/client.py` 只保留统一调用入口、Hook 与错误收口；协议级转换统一收敛在 `adapters/llm/protocols.py`，厂商差异统一收敛在 `adapters/llm/vendors.py`，禁止继续把 `qwen` / `kimi` 等厂商分支散落回 `client.py` 或 `runtime/session.py`。
- `runtime/agents.py` 是 agent 元信息唯一来源；每个 agent 必须声明 `model`（`primary`/`subagent`）和 `description`。
- 工具实现统一在 `tools/` 目录内分模块维护；bash 相关逻辑放 `tools/bash_tool.py`，`read_file` 放 `tools/read_file_tool.py`，`write_file` 放 `tools/write_file_tool.py`，`edit_file` 放 `tools/edit_file_tool.py`，`glob` 放 `tools/glob_tool.py`，`grep` 放 `tools/grep_tool.py`，`question` 放 `tools/question_tool.py`，`load_skill` 放 `tools/skill_tool.py`，路径解析公共逻辑放 `tools/path_utils.py`，其余通用工具默认放 `tools/handlers.py`，工具协议统一在 `tools/specs.py`。
- 文件工具与 plan 模式拦截默认返回结构化结果，至少包含 `output` 与 `metadata.status`；失败场景应补充 `metadata.error_code`。
- `glob` 的正式参数统一为 `pattern` 与可选 `path`；`path` 未传时默认使用工作区根目录，仅允许搜索工作区内已存在目录，结果只返回普通文件，按文件修改时间倒序排列，最多返回 `100` 条。
- `grep` 的正式参数统一为 `pattern` 与可选 `path`、`include`；`path` 未传时默认使用工作区根目录，仅允许搜索工作区内已存在目录，`include` 用于限制搜索文件范围；结果返回命中的文件路径、行号与行内容，按命中文件修改时间倒序、同文件内按行号升序排列，最多返回 `100` 条。
- `read_file` 的正式参数统一为 `file_path`，且必须使用绝对路径；允许读取的范围仅限当前工作区、`~/.my-agent/skills` 下的 skill 文件，以及当前 session 对应的 `plan`、`tool-output`、`sessions` 运行态文件，禁止跨 session 读取其他运行态数据。
- `write_file` 的正式参数统一为 `filePath` 与 `content`；语义为整文件覆盖写入，正式建议传绝对路径，若传相对路径则按工作区根目录解析；允许写入当前工作区与 `~/.my-agent/skills`；对已存在文件，写入前必须先对同一文件执行 `read_file`，并以最近一次读取时记录的 `mtime_ns` 校验文件未发生变化；若文件在读取后又被修改，必须重新读取后再写入。
- `write_file` 当前返回 `metadata.filepath`、`metadata.exists`、`metadata.diagnostics` 与 `metadata.diagnostics_status`；其中 diagnostics 仅预留按语言接入 language service 的统一入口，本期固定返回空数组与 `not_enabled`。
- `edit_file` 的正式参数统一为 `filePath`、`oldString`、`newString` 与可选 `replaceAll`；允许编辑当前工作区与 `~/.my-agent/skills` 的文本文件；编辑已有文件前必须先对同一文件执行 `read_file`，并以最近一次读取时记录的 `mtime_ns` 校验文件未发生变化；若文件在读取后又被修改，必须重新读取后再编辑。
- `edit_file` 当前返回 `metadata.diff`、`metadata.filediff`、`metadata.diagnostics` 与 `metadata.diagnostics_status`；其中 diagnostics 仅预留按语言接入 language server 的统一入口，本期固定返回空数组与 `not_enabled`。
- `load_skill` 的正式参数统一为 `name`；工具会按名称精确加载单个 skill，并返回 `title/output/metadata` 结构，其中 `output` 必须包含 `## Skill: {name}`、`Base directory: {dir}` 与原始 `SKILL.md` 全文，禁止模型再通过 `glob`、`grep`、`bash` 自行搜索 skill 目录。
- 工作区根目录统一由启动命令所在目录或 `--workdir` 指定目录决定，禁止在业务模块继续散落使用 `Path.cwd()` 或固定仓库根目录推导工作区边界。
- 很多工具都会验证工作路径是否合法；相对路径解析、工作区越界校验、目录存在性校验等公共逻辑必须统一收敛到 `tools/path_utils.py`，禁止继续在各工具模块重复实现。
- plan 模式占位文件统一落到当前会话对应的 `~/.my-agent/workspaces/plan/<session_id>.md`，plan 模式下仅允许写入该文件。
- 会话运行态数据写入 `~/.my-agent/workspaces/sessions/`；todo、tool-output 等共享运行态数据按类型聚合写入 `~/.my-agent/workspaces/todo/<session_id>.json` 与 `~/.my-agent/workspaces/tool-output/<session_id>/`，禁止继续写回仓库内 `src/storage/*`。
- `plan_enter` / `plan_exit` 只允许发起切换申请，确认与取消必须由程序侧状态机控制，禁止继续通过 LLM 参数决定。
- Web 端“确认切换”必须通过流式接口继续执行确认后的会话，避免阻塞式请求导致界面无法实时更新。
- Web 端允许通过 `POST /api/sessions/{session_id}/stop` 停止当前会话；运行时必须按 `session_id` 管理停止标记，并在 loop 顶部优先检查，命中后以 `interrupted/cancelled` 统一收口。
- `question` 工具用于向用户发起结构化问题；运行时必须按 `session_id` 管理待答问题，并通过独立的 Web 答题/拒绝接口恢复执行；Web 答题接口固定提交“每题 `answers[] + notes`”的结构化结果，恢复给 LLM 的输入必须明确区分选项与备注。
- `question` 工具实现统一收敛在 `tools/question_tool.py`；问题项支持可选 `custom` 字段，默认 `true`，命中时由后端统一自动追加“不是以上任何选项”兜底项，禁止模型手写重复兜底选项。
- `load_skill` 工具实现统一收敛在 `tools/skill_tool.py`；工具描述模板统一放在 `tools/load_skill.txt`；`runtime/session.py` 只做路由，禁止继续内联 skill 正文拼装逻辑。
- skills 的可用目录统一通过 `load_skill` 工具描述动态暴露，skills 正式根目录固定为 `~/.my-agent/skills`（若配置 `MY_AGENT_HOME`，则使用对应目录下的 `skills/`），禁止继续在 agent prompt 中注入 `skills_catalog`。
- 主 Agent 模式状态统一收敛在 `runtime/main_agent_mode.py`（若启用该模块），禁止散落存储。
- 子 Agent 扩展统一通过 `task` 工具路由，不在会话层写业务分支。
- `task` 工具中的 subagent 名单与说明，必须从 `runtime/agents.py` 动态生成，禁止在 `specs.py` 或 `session.py` 中手写支持列表。
- `bash` 工具描述模板统一放在 `src/agent/tools/bash.txt`；当工具说明较长时，必须优先拆到独立 `.txt` 模板文件中，禁止继续在 `specs.py` 内联大段说明。
- Web 时间线必须按 `session` 维度累计展示，禁止在前端新一轮提交时覆盖上一轮执行轨迹。
- `task` 委派 subagent 时，流式事件必须透传 subagent 内部进度，并使用后端生成的 `delegation_id` 作为稳定关联键。
- Web 助手消息展示必须优先基于后端返回的 `display_parts` 顺序片段流，确保 `assistant_text` 与 `tool_call`/`tool_result` 按真实发生顺序穿插；仅在旧消息缺少该字段时才回退到 `process_items + text` 的兼容渲染。
- Web 序列化逻辑统一收敛在 `web/serializers.py`，禁止在 `web/app.py` 中继续大段手工映射 `MessageVO` 或 SSE payload。
- 日志必须通过程序显式传递 `agent`、`model` 等上下文字段，禁止依赖 LLM 生成或推断日志元信息。
- 业务正常链路日志仅保留 LLM 调用前后、工具调用前后；其余调试日志默认不落盘。
- 日志文件统一写入 `~/.my-agent/logs/app-YYYY-MM-dd.log`，并使用追加模式保留历史内容；如配置 `MY_AGENT_HOME`，则写入对应目录。
- 日志截断策略必须统一读取 `project_runtime.json -> logging`；默认 `truncate_enabled=false`，即不截断普通日志文本，但仍保留换行转义与敏感信息脱敏。
- `build` 主模式的提示词文件必须按厂商 `vendor` 选择，命名统一为 `build.<vendor>.txt`；厂商归属在 `src/agent/config/llm_runtime.json` 中显式声明，缺省时回退 `build.default.txt`。
- `llm_runtime.json` 的 provider 配置必须采用“厂商公共配置 + 多模型列表”结构：显式声明 `default_model`、`models` 与 `api_mode`；`agent_defaults` 必须显式声明 `provider + model`，禁止继续使用“一个 provider 绑定一个 model”的旧结构。
- `api_mode` 当前支持 `responses` 与 `chat_completions`；其中 `responses` 已接入真实 `/v1/responses` 调用链，覆盖非流式、流式、函数工具调用与工具结果回灌，仍保持仓库侧显式维护消息历史，不引入 `previous_response_id` 隐式会话状态。
- `qwen` 在 `api_mode=responses` 下必须使用 DashScope Responses 兼容入口 `https://dashscope.aliyuncs.com/api/v2/apps/protocols/compatible-mode/v1`；禁止继续沿用 `chat.completions` 的旧兼容入口 `https://dashscope.aliyuncs.com/compatible-mode/v1`。
- Web 端运行时选择必须支持 `provider / model` 组合，而不是仅选择 provider；前端提交会话请求时必须同时传递 `provider` 与 `model`，运行时需按完整组合跨轮记忆显式选择。
- `kimi` provider 统一走 Moonshot OpenAI 兼容接口，`base_url` 固定为 `https://api.moonshot.cn/v1`，API Key 环境变量统一使用 `KIMI_API_KEY`。
- 项目级运行时开关统一放在 `src/agent/config/project_runtime.json`，禁止继续在 `runtime/compaction.py` 等业务模块中硬编码可配置策略。
- `project_runtime.json` 中的 `compaction` 必须采用 `default + vendors` 结构；命中当前模型厂商 `vendor` 时，仅覆盖显式配置字段，未配置字段继续继承 `default`。
- `project_runtime.json` 中的 `file_extraction` 也必须采用 `default + vendors` 结构；当前默认仅开放 `.pdf`，并统一使用 `cleanup_mode=async_delete` 做远端异步清理。
- `project_runtime.json` 中的 `agent_loop.max_rounds` 是主 Agent 循环的统一兜底阈值；命中上限后必须以 `finish_reason=error` 收口，禁止继续无限空转。
- `compaction.tool_result_keep_recent` 的计数口径统一按 `role=tool` 消息数量计算，默认保留最近 `3` 条不压缩。
- 当前可配置的压缩关键参数包括：`tool_result_prune_enabled`、`tool_result_keep_recent`、`tool_result_prune_min_chars`、`summary_trigger_threshold`、`summary_max_tokens`、`tool_output_max_lines`、`tool_output_max_bytes`。
- LLM 响应转换后的 assistant message 必须统一写入标准化 `finish_reason`；session loop 只允许基于该字段决定继续或停止，禁止继续直接用“是否存在 tool_call”充当终止规则。
- `kimi` 命中 PDF 附件时，必须按 Moonshot 官方文件抽取链路处理：先上传 `/v1/files`，再读取 `/v1/files/{file_id}/content`，最后把抽取文本包装为“仅供参考”的合成 `user` message 注入 `chat.completions`；远端删除失败仅记日志，不得阻塞主流程。

## 开发与验证命令

- `pip install -e .`：安装 `my-agent` 命令。
- `my-agent`：在当前目录启动 CLI。
- `my-agent web --host 127.0.0.1 --port 8000`：在当前目录启动 Web 后端。
- `python3 src/main.py`：兼容 CLI 入口。
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

- 2026-03-27：增强 session 历史恢复链路；`session_memory` 在读取历史时会为非法前缀自动补齐 synthetic user 锚点，`runtime/session.py` 新增对“首条 assistant(tool_calls)”“首条 tool”“中间孤儿 tool”的统一修理逻辑：孤儿 `tool` 会补 synthetic assistant(tool_calls) 作为上下文锚点，缺失的 tool result 会补“具体情况未知”的 synthetic tool 占位结果；`chat_completions` 协议层同时收紧校验，若仍出现未修理的孤儿 `tool`，会在本地直接报 `invalid_tool_message_sequence`，避免继续外发非法消息序列。
- 2026-03-26：新增 `project_runtime.logging` 配置，统一控制日志文本是否截断与截断长度；默认关闭截断，仅保留换行转义与敏感信息脱敏，便于排查超长 tool 参数与模型返回内容。
- 2026-03-26：新增独立 `src/agent/tools/skill_tool.py` 与 `load_skill.txt`，将 `load_skill` 重构为按 `name` 精确加载单个 skill 的独立工具；返回结构统一为 `title/output/metadata(name, dir)`，`output` 仅注入 `Base directory` 与原始 `SKILL.md`，避免模型再用 `glob`/`bash` 搜索 skill 目录。
- 2026-03-26：新增独立 `src/agent/tools/question_tool.py`，将 `question` 工具从 `handlers.py` 拆分；问题项新增可选 `custom` 字段，默认 `true`，由后端统一自动追加“不是以上任何选项”兜底项，并同步补齐 runtime/Web/schema 透传与测试覆盖。
- 2026-03-26：新增 `question` 工具与 `src/agent/tools/question.txt`，支持 Agent 在执行过程中发起结构化提问；运行时新增待答问题状态管理、Web 答题/拒绝接口与流式恢复链路，助手消息与 SSE `done` 事件同步透传 `question` 结构，用户拒绝回答时会以“用户拒绝”语义继续后续推理并与普通工具异常区分。
- 2026-03-26：Web 前端补齐 `question_required` 交互：最新待答问题会切换输入区为 question composer，支持问题/选项/notes 三段式输入、上下左右键导航、`Tab` 焦点切换、`Shift+Enter` 备注换行，并将每题 `answers + notes` 以结构化 payload 提交到 question 恢复接口。
- 2026-03-26：skills 正式根目录切换为 `~/.my-agent/skills`；`read_file` / `write_file` / `edit_file` 新增对 skills 目录的受控访问；同时修复 `kimi` 在 `chat_completions` 下插入 PDF 抽取上下文过早导致多工具调用 `tool_call_id` 与 `tool` 响应不成对的问题，并新增本地序列校验防止无效请求直接外呼。
- 2026-03-26：新增独立 `src/agent/tools/write_file_tool.py` 与 `write_file.txt`，将 `write_file` 收敛为整文件覆盖写工具；正式参数切换为 `filePath/content`，新增“已有文件先 `read_file` 再写”的强校验、基于 `mtime_ns` 的读后变更保护，以及 `filepath/exists/diagnostics_status` 结构化返回。
- 2026-03-26：重构 `edit_file` 为独立 `src/agent/tools/edit_file_tool.py`，正式参数切换为 `filePath/oldString/newString/replaceAll`，新增“先读后改”强校验、基于 `mtime_ns` 的读后变更保护、保守多层文本匹配与 `diff/filediff/diagnostics_status` 结构化返回；同时新增 `src/agent/tools/file_edit_state.py` 记录当前 session 的文件读取/编辑时序状态。
- 2026-03-25：新增 `src/agent/tools/grep_tool.py` 与 `grep` 工具，基于 ripgrep 在工作区内做内容正则搜索，支持 `pattern/path/include` 入参，结果按命中文件修改时间倒序、同文件内按行号升序返回，并统一复用结构化工具返回协议。
- 2026-03-25：新增 `src/agent/tools/glob_tool.py` 与 `glob` 工具，支持在工作区内按 glob 模式搜索普通文件、按修改时间倒序返回并最多截断 100 条；同时新增 `src/agent/tools/path_utils.py`，统一收敛工作区路径解析与目录校验逻辑，复用到 `bash` 与文件工具。
- 2026-03-25：将 `read_file` 从 `handlers.py` 拆分到独立 `src/agent/tools/read_file_tool.py`，正式参数统一为 `file_path` 且仅支持绝对路径；同时新增当前 session 运行态白名单，允许读取当前 session 的 `plan`、`tool-output` 与 `sessions` 文件，并同步收敛工具描述、Schema 与测试。
- 2026-03-23：修复 `bash` 工具链路的 3 个问题：`PersistentBashSession` 读取输出时改为增量查找 marker，避免大输出场景反复拼接全量缓冲区导致性能退化；`specs.py` 在构建 bash 工具描述时补齐 `bash.txt` 中 `${directory}`、`${maxLines}`、`${maxBytes}` 的实际替换；`session.py` 对非法 `timeout` 参数新增结构化失败返回 `bash_timeout_invalid`，不再落成泛化 `execution_error`。
- 2026-03-23：`bash` 工具改为“单次调用内持久、调用结束即销毁”的持久 bash shell；同一次调用中的多步命令共享目录与环境变量状态，不再跨调用复用 shell，同时移除工具层固定字符截断，改为复用运行时统一长输出落盘链路，保持与 `src/agent/tools/bash.txt` 文档一致。
- 2026-03-23：新增 `src/skills/api-pdf-md-first/` skill，用于“PDF 接口文档先转同目录 `.api.md`，后续多轮开发优先复用 Markdown”场景，降低长会话重复读取 PDF 的 token 消耗，并补充接口 Markdown 模板与提炼规则。
- 2026-03-23：重构 `bash` 工具协议，新增必填 `description` 与可选 `timeout`、`workdir` 入参，默认超时收敛为 `DEFAULT_TIMEOUT=120000ms`，默认执行目录固定为当前工作区根目录，并将工具 description 拆分到独立 `src/agent/tools/bash.txt` 模板文件。
- 2026-03-23：Web 前端新增 LLM reasoning 展示；后端流式链路增加 `reasoning_delta` 事件并将 `display_parts.kind=reasoning` 显式透传，前端按时间线顺序内联展示 reasoning，支持单条折叠与全局默认展开/收起控制，同时保持 reasoning 与最终回答分离存储和渲染。
- 2026-03-23：增强 `llm.response` 日志，统一打印标准化 `finish_reason`、响应文本、思考内容与工具调用摘要，避免模型仅返回 thinking 或 tool call 时日志只剩空 `message=`，提升排障可观测性。
- 2026-03-23：重构 assistant message 的标准化 `finish_reason` 与 agent loop 终止规则；`chat_completions` / `responses` 统一映射为 `stop`、`length`、`content-filter`、`tool-calls`、`unknown`、`error`，session loop 改为仅基于该字段推进，并新增 `project_runtime.json -> agent_loop.max_rounds` 兜底未知态空转。
- 2026-03-20：移除运行时内部对 `default_session` 的隐式兜底；Web / 运行时正式入口缺少 `session_id` 时直接报错，CLI 与测试入口改为在最外层自动生成随机 `session_id` 后再进入会话链路。
- 2026-03-20：修复 `kimi` PDF 抽取在多轮会话中的重复上传与提示词乱序问题；同一 PDF 在首次抽取后会把结果缓存到原 `tool` 输出元数据，后续轮次直接复用，并将“仅供参考”的合成 `user` message 固定插入到对应 `tool` 消息之后，不再整体前置到对话最前面。
- 2026-03-20：为 `kimi` provider 新增 PDF 支持；命中 PDF 附件时改为走 Moonshot 文件抽取链路（上传文件、拉取抽取文本、注入“仅供参考”的合成 `user` message），并在抽取完成后异步删除远端文件，删除失败不影响主流程。
- 2026-03-20：`project_runtime.json` 新增 `file_extraction` 配置，统一管理可抽取文件扩展名与清理策略，当前默认仅开放 `.pdf`。
- 2026-03-20：`llm.request` 日志新增文件附件摘要输出；当请求历史中包含 tool result 附件时，日志会打印精简的 `role:mime:filename` 摘要，避免继续只显示文本请求且不输出 base64 正文。
- 2026-03-20：`qwen` 在 `api_mode=responses` 下命中文件附件输入时改为本地直接返回 `unsupported_file_input` 错误，不再继续把 PDF 等文件按 OpenAI `input_file` 结构发送到 DashScope，避免无效外呼与 500 校验错误。
- 2026-03-20：`read_file` 新增 PDF 支持；读取 PDF 时返回固定文本 `PDF read successfully`，并将 PDF 内容以内联 base64 data URL 的 `attachments` 结构挂到 tool result。OpenAI `responses` 组装阶段会将该 PDF 附件翻译为 `function_call_output` 中的 `input_file`，`chat_completions` 暂保留文本回退。
- 2026-03-20：修复 `qwen` 在 `api_mode=responses` 下的无参 function tool schema 兼容问题；无参工具不再下发 `parameters: {}`，避免 DashScope 返回 `InternalError.Algo.InvalidParameter`。
- 2026-03-20：为 `qwen` 的 `responses` 独立收敛 function tool 参数 schema，避免继续复用 OpenAI 更严格的 `additionalProperties/default/strict` 规范化结果导致 DashScope 兼容层报 `InternalError.Algo.InvalidParameter`。
- 2026-03-20：将 LLM 适配层重构为“统一入口 + 协议层 + 厂商方言层”，新增 `adapters/llm/protocols.py` 与 `adapters/llm/vendors.py`，在不改变 `Message` 结构的前提下为 `qwen` / `kimi` 保留独立转换扩展点。
- 2026-03-20：将 `qwen` provider 在 `api_mode=responses` 下的 `base_url` 修正为 DashScope Responses 兼容入口 `https://dashscope.aliyuncs.com/api/v2/apps/protocols/compatible-mode/v1`，避免继续误用旧的 `chat.completions` 兼容入口导致请求失败。
- 2026-03-20：OpenAI `gpt` provider 的 `api_mode=responses` 已接入真实 `/v1/responses` 调用链，补齐非流式、流式、函数工具调用与工具结果回灌适配，继续由仓库自行维护多轮历史，不引入 `previous_response_id` 隐式状态。
- 2026-03-20：将 Web 端运行时选择从仅 provider 下拉升级为 `provider / model` 组合下拉；`/api/chat/stream` 新增 `model` 入参，session 运行时改为跨轮记忆完整的 `provider + model` 显式选择。
- 2026-03-20：重构 `llm_runtime.json` 为 provider 多模型结构，新增 `default_model`、`models` 与 `api_mode` 解析；`ResolvedLLMConfig` 增加 `api_mode`，并在 LLM client 中预留协议分流入口，同时保持当前 `chat.completions` 调用链不变。
- 2026-03-19：将“停止后继续”收敛为基于真实会话历史的正常多轮续接，移除专用 resume 恢复提示注入；点击停止后即使前端提前断流，后端也必须补齐 `interrupted/cancelled` 助手收尾消息并持久化，避免后续请求依赖额外恢复文件。
- 2026-03-19：修复 Web 停止后继续执行的恢复链路，补充顶层执行 stop 清理、最近中断任务恢复上下文，以及前端停止等待收口后再超时兜底断流，避免残留“当前执行已手动停止。”污染下一轮并降低继续任务时的上下文丢失。
- 2026-03-19：将 session 历史落库目录调整为全局 `~/.my-agent/workspaces/sessions/`，文件名直接使用 `session_id` 安全清洗后的结果，不再按工作区目录隔离。
- 2026-03-19：将 `todo`、`plan` 与 `tool-output` 的运行态路径统一切换为按 `session_id` 组织，移除路径中的 `workspace_id`。
- 2026-03-19：新增可选 `kimi` provider，统一通过 `KIMI_API_KEY` 读取 Moonshot OpenAI 兼容接口密钥，默认模型名先保留占位值，待按实际账号可用模型补齐。
- 2026-03-18：完成第一阶段运行时重构，收敛 `session.py` 中的会话初始化、`task` 工具参数解析与模式切换结果处理重复逻辑，并补充非法 `task` 参数回归测试。
- 2026-03-18：完成第二阶段运行时重构，将流式事件、`process_items`、`display_parts` 与响应摘要汇总逻辑抽离到独立的 `runtime/stream_display.py`，降低 `session.py` 与展示层的耦合。
- 2026-03-18：完成第三阶段工具层重构，统一文件工具与 plan 模式拦截的结构化返回，补充 `handlers` 成功/失败结果与错误码回归测试，并保持 tool 输出文本兼容原行为。
- 2026-03-18：完成第四阶段 Web 重构，将 `Message -> VO` 与 SSE 事件序列化抽离到 `web/serializers.py`，降低 `app.py` 中的手工字段映射与重复流式封装逻辑。
- 2026-03-18：完成第五阶段收口整理，清理 `session.py` 中的未使用局部变量与 `handlers.py` 的无用日志对象，并为工具结果构造与 Web 序列化补充关键注释。
- 2026-03-18：新增正式 CLI 入口 `agent.cli` 与 `pyproject.toml`，支持在任意目录通过 `my-agent` / `my-agent web` 启动并将当前目录绑定为工作区。
- 2026-03-18：新增 `runtime/workspace.py`，统一工作区根目录、运行态目录、`AGENTS.md` 发现逻辑与 Web/CLI 启动模式。
- 2026-03-18：将会话、todo、plan 占位文件、tool-output 与日志迁移为按工作区隔离的 `~/.my-agent/` 目录结构。
- 2026-03-18：将 `plan` 与 `todo` 调整为按类型聚合的工作区单文件，并将 `tool-output` 调整为按类型聚合的工作区子目录，统一运行态路径语义。
- 2026-03-18：新增会话停止能力与前端停止按钮，支持按 `session_id` 协作式终止当前 loop，并统一落 `interrupted/cancelled` 助手消息。
