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
- `src/agent/runtime/web_dev_server.py`：`my-agent web` 的前后端联合启动、依赖校验与子进程托管。
- `src/agent/runtime/workspace.py`：工作区根目录、运行态目录与启动模式解析。
- `src/agent/adapters/llm/client.py`：LLM 统一调用入口与 LLM Hook。
- `src/agent/adapters/llm/protocols.py`：协议层适配（`responses` / `chat_completions`）。
- `src/agent/adapters/llm/vendors.py`：厂商方言注册与独立转换层。
- `src/agent/config/logging_setup.py`：统一日志初始化、格式规范与日志脱敏。
- `src/agent/config/project_runtime.json`：项目级运行时配置（如压缩开关、保留策略）。
- `src/agent/lsp/`：LSP 统一入口、server 生命周期、文档同步、诊断过滤与语言适配层。
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
- `write_file` 成功写入后必须尝试走统一 `LSP Client -> documents -> diagnostics filters` 链路；工具层只消费结构化诊断结果，不感知 `jdtls`、JSON-RPC 或文档版本细节；返回至少包含 `metadata.filepath`、`metadata.exists`、`metadata.diagnostics`、`metadata.diagnostics_status`，并在存在关键错误时把错误摘要直接追加进 `output`。
- `edit_file` 的正式参数统一为 `filePath`、`oldString`、`newString` 与可选 `replaceAll`；允许编辑当前工作区与 `~/.my-agent/skills` 的文本文件；编辑已有文件前必须先对同一文件执行 `read_file`，并以最近一次读取时记录的 `mtime_ns` 校验文件未发生变化；若文件在读取后又被修改，必须重新读取后再编辑。
- `edit_file` 成功写入后同样必须触发统一 LSP 诊断链路；当前仅回填“当前文件”的 diagnostics，不做跨文件全量回灌；LSP 不可用时只通过 `metadata.diagnostics_status/lsp_error` 与 `output` 追加说明降级，不改变工具成功语义。
- `load_skill` 的正式参数统一为 `name`；工具会按名称精确加载单个 skill，并返回 `title/output/metadata` 结构，其中 `output` 必须包含 `## Skill: {name}`、`Base directory: {dir}` 与原始 `SKILL.md` 全文，禁止模型再通过 `glob`、`grep`、`bash` 自行搜索 skill 目录。
- 工作区根目录统一由启动命令所在目录或 `--workdir` 指定目录决定，禁止在业务模块继续散落使用 `Path.cwd()` 或固定仓库根目录推导工作区边界。
- `my-agent web` 默认同时启动后端 uvicorn 与前端 Vite 开发服务；前端目录定位、`pnpm`/`node_modules` 校验、端口就绪探测与子进程清理由 `runtime/web_dev_server.py` 统一负责，禁止回退到 `cli.py` 内联零散进程管理。
- system prompt 组装时必须先尝试追加全局 `~/.my-agent/AGENTS.md`，再追加当前工作区 `AGENTS.md`；任一文件不存在、为空或读取失败时都应自动忽略，避免影响主流程。
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
- 会话历史条数裁剪策略必须统一读取 `project_runtime.json -> session_memory`；默认 `trim_enabled=true`、`max_messages=24`，即最多保留最近 `24` 条非 `system` 消息。
- `build` 主模式的提示词文件必须按厂商 `vendor` 选择，命名统一为 `build.<vendor>.txt`；厂商归属在 `src/agent/config/llm_runtime.json` 中显式声明，缺省时回退 `build.default.txt`。
- `llm_runtime.json` 的 provider 配置必须采用“厂商公共配置 + 多模型列表”结构：显式声明 `default_model`、`models` 与 `api_mode`；`agent_defaults` 必须显式声明 `provider + model`，禁止继续使用“一个 provider 绑定一个 model”的旧结构。
- `api_mode` 当前支持 `responses` 与 `chat_completions`；其中 `responses` 已接入真实 `/v1/responses` 调用链，覆盖非流式、流式、函数工具调用与工具结果回灌，仍保持仓库侧显式维护消息历史，不引入 `previous_response_id` 隐式会话状态。
- `qwen` 在 `api_mode=responses` 下必须使用 DashScope Responses 兼容入口 `https://dashscope.aliyuncs.com/api/v2/apps/protocols/compatible-mode/v1`；禁止继续沿用 `chat.completions` 的旧兼容入口 `https://dashscope.aliyuncs.com/compatible-mode/v1`。
- Web 端运行时选择必须支持 `provider / model` 组合，而不是仅选择 provider；前端提交会话请求时必须同时传递 `provider` 与 `model`，运行时需按完整组合跨轮记忆显式选择。
- `kimi` provider 统一走 Moonshot OpenAI 兼容接口，`base_url` 固定为 `https://api.moonshot.cn/v1`，API Key 环境变量统一使用 `KIMI_API_KEY`。
- 项目级运行时开关统一放在 `src/agent/config/project_runtime.json`，禁止继续在 `runtime/compaction.py` 等业务模块中硬编码可配置策略。
- `project_runtime.json` 中的 `compaction` 必须采用 `default + vendors` 结构；命中当前模型厂商 `vendor` 时，仅覆盖显式配置字段，未配置字段继续继承 `default`。
- `project_runtime.json` 中的 `file_extraction` 也必须采用 `default + vendors` 结构；当前默认仅开放 `.pdf`，并统一使用 `cleanup_mode=async_delete` 做远端异步清理。
- `project_runtime.json` 中的 `lsp` 统一管理语言服务开关、超时、诊断裁剪、语言命令与 IDE 代理预留配置；语言扩展必须优先新增 `lsp.languages.<lang>` 与 `src/agent/lsp/servers/*.py` 适配器，禁止把语言分支散落回工具层。Java `jdtls` 默认要求通过 `lsp.languages.java.command` 显式绑定 JDK 21，避免依赖当前 shell 激活的默认 Java 版本。
- `project_runtime.json` 中的 `agent_loop.max_rounds` 是主 Agent 循环的统一兜底阈值；命中上限后必须以 `finish_reason=error` 收口，禁止继续无限空转。
- `project_runtime.json` 中的 `session_memory` 负责会话历史按消息条数的裁剪策略；`trim_enabled=false` 仅关闭条数裁剪，不关闭 compaction checkpoint 收口与非法前缀修复。
- `compaction.tool_result_keep_recent` 的计数口径统一按 `role=tool` 消息数量计算，默认保留最近 `3` 条不压缩。
- 当前可配置的压缩关键参数包括：`tool_result_prune_enabled`、`tool_result_keep_recent`、`tool_result_prune_min_chars`、`summary_trigger_threshold`、`summary_max_tokens`、`tool_output_max_lines`、`tool_output_max_bytes`。
- LLM 响应转换后的 assistant message 必须统一写入标准化 `finish_reason`；session loop 只允许基于该字段决定继续或停止，禁止继续直接用“是否存在 tool_call”充当终止规则。
- `kimi` 命中 PDF 附件时，必须按 Moonshot 官方文件抽取链路处理：先上传 `/v1/files`，再读取 `/v1/files/{file_id}/content`，最后把抽取文本包装为“仅供参考”的合成 `user` message 注入 `chat.completions`；远端删除失败仅记日志，不得阻塞主流程。

## 开发与验证命令

- `pip install -e .`：安装 `my-agent` 命令。
- `my-agent`：在当前目录启动 CLI。
- `my-agent web --host 127.0.0.1 --port 8000`：在当前目录一键静默启动 Web 前后端；首次启动前需先执行 `cd frontend && pnpm install`，如需控制台输出可追加 `--verbose`。
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