# 扩展开发指南

本文档面向仓库开发者，说明如何在当前架构下扩展工具、Subagent、Hook 和 Web 输出。

## 新增 Slash Command

1. 在 `src/agent/slash_commands/registry.py` 注册命令元信息。
2. 在 `src/agent/slash_commands/resolver.py` 增加命令解析后的执行编排。
3. 如需稳定 prompt，优先在 `src/agent/slash_commands/prompts/` 下新增模板文件。
4. Web 层命令展示统一消费 `build_runtime_options()` 返回的 `slash_commands`，不要在前端写死命令列表。
5. `runtime/session.py` 只保留命令预处理接入点，不承载命令明细规则。

## 新增工具

1. 在 `src/agent/tools/` 下新增或扩展对应模块。
2. 在 `src/agent/tools/specs.py` 中补充工具 schema 与描述。
3. 在 `src/agent/runtime/session.py` 中注册工具路由。
4. 工具返回应优先保持结构化，至少包含：
   - `output`
   - `metadata.status`
5. 在 `tests/` 中补齐：
   - 成功路径
   - 参数异常
   - 安全边界

推荐职责划分：

- `read_file`：`src/agent/tools/read_file_tool.py`
- `write_file`：`src/agent/tools/write_file_tool.py`
- `edit_file`：`src/agent/tools/edit_file_tool.py`
- `glob`：`src/agent/tools/glob_tool.py`
- `grep`：`src/agent/tools/grep_tool.py`
- `question`：`src/agent/tools/question_tool.py`
- `load_skill`：`src/agent/tools/skill_tool.py`
- 通用路径逻辑：`src/agent/tools/path_utils.py`

## 新增 Subagent

1. 在 `src/agent/runtime/agents.py` 注册 agent。
2. 声明 `model="subagent"` 与清晰 `description`。
3. 在 `src/agent/runtime/prompts/` 下提供同名 prompt 文件。
4. 如无特殊需求，复用基础工具集合；新增能力优先在工具层扩展，不在会话层写死分支。
5. 在 `tests/test_run_session.py` 中增加路由与可见性测试。

## 新增 Tool Hook

1. 继承 `src/agent/runtime/tool_executor.py` 中的 `ToolHook`。
2. 按需实现：
   - `before_call`
   - `after_call`
   - `on_error`
3. 通过全局注册或 `run_session(..., tool_hooks=[...])` 注入。

## 新增 LLM Hook

1. 继承 `src/agent/adapters/llm/client.py` 中的 `LLMHook`。
2. 在调用前后添加观测、审计或脱敏逻辑。
3. 通过全局注册生效。

## 调整 Web 输出

1. 新增展示字段时，优先修改：
   - `src/agent/web/schemas.py`
   - `src/agent/web/serializers.py`
2. `src/agent/web/app.py` 只保留：
   - 路由
   - 参数校验
   - HTTP/SSE 响应封装
3. 不要在路由函数中散落手工字段映射。

## 测试建议

涉及以下改动时，建议至少补齐对应测试：

- 工具协议或行为变更：`tests/test_handlers.py`、`tests/test_run_session.py`
- Web API 变更：`tests/test_web_api.py`
- Agent / subagent 扩展：`tests/test_run_session.py`
- 安全边界变更：路径穿越、危险命令、超时、权限限制相关测试

## 设计建议

- 优先保持职责单一，避免把业务逻辑重新堆回 `runtime/session.py`
- 纯逻辑与副作用分离，方便测试
- 新增协议字段时优先考虑兼容已有前端和日志链路
- 配置优先走 `llm_runtime.json` 或 `project_runtime.json`，避免在业务代码中扩散硬编码
