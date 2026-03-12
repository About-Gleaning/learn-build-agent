# my-main-agent

一个按分层架构组织的 Python Agent 项目，重点是可维护、可扩展与安全可控。

## 核心目标

- 分层清晰：运行时编排、工具实现、LLM 适配职责分离。
- 扩展友好：工具协议、Hook 机制、主/子 Agent 路由可复用。
- 安全优先：路径边界校验、命令执行限制、模式隔离。

## 目录结构

```text
src/
  main.py                         # CLI 轻量入口（示例运行）
  web_main.py                     # FastAPI 启动入口（uvicorn 使用）
  agent/
    config/
      settings.py                 # 环境与配置读取
    core/
      context.py                  # 会话上下文（ContextVar）
      message.py                  # 统一 Message/Part 协议与转换
      hooks.py                    # 通用 HookDispatcher
    adapters/
      llm/
        client.py                 # LLM 调用适配与 LLM Hook
    runtime/
      session.py                  # 会话主循环与工具调用编排
      session_memory.py           # 会话记忆与状态持久化辅助
      tool_executor.py            # ToolExecutor 与 Tool Hook 调度
      compaction.py               # 上下文压缩
    web/
      app.py                      # Web API（SSE 聊天、历史查询、清空会话）
      schemas.py                  # Web 层请求/响应模型
    tools/
      handlers.py                 # 各工具业务实现
      specs.py                    # 工具协议定义
      todo_manager.py             # todo 状态管理与持久化
      todo_write.txt              # todo_write 工具描述
    skills/
      runtime.py                  # skills 发现、解析、按需加载
tests/
  test_*.py                       # 回归与安全边界测试
frontend/
  src/                            # React + TypeScript 前端页面
```

## 快速开始

1. 准备环境变量：复制 `.env.example` 为 `.env`，并配置 `API_KEY`。
2. 安装依赖：`pip install -r requirements.txt`。
3. 运行 CLI 示例：`python3 src/main.py`。
4. 启动 Web 后端：`uvicorn src.web_main:app --reload --host 127.0.0.1 --port 8000`。
5. 启动前端：

```bash
cd frontend
cp .env.example .env
pnpm install
pnpm dev
```

6. 运行测试：`pytest -q`。
7. 语法检查：`python3 -m py_compile $(find src -name '*.py')`。

## 分层职责约束（必须遵守）

- `runtime/session.py` 仅做会话编排（消息循环、模式切换、工具分发），不放工具业务逻辑。
- 工具实现统一放在 `tools/handlers.py`，工具协议统一放在 `tools/specs.py`。
- 主 Agent 模式状态统一放在 `runtime/main_agent_mode.py`（若新增），禁止散落存储。
- 子 Agent 统一通过 `task` 工具路由，不在会话层硬编码分支逻辑。

## 扩展指南

### 1) 新增工具

1. 在 `src/agent/tools/handlers.py` 增加实现。
2. 在 `src/agent/tools/specs.py` 增加 JSON Schema。
3. 在 `src/agent/runtime/session.py` 的工具映射中注册（仅路由）。
4. 在 `tests/` 补齐测试：成功路径、参数异常、安全边界。

### 2) 新增 Tool Hook

1. 继承 `src/agent/runtime/tool_executor.py` 中的 `ToolHook`。
2. 实现 `before_call`、`after_call`、`on_error` 任一或多个阶段。
3. 通过 `register_global_tool_hook()` 或 `run_session(..., tool_hooks=[...])` 注入。

### 3) 新增 LLM Hook

1. 继承 `src/agent/adapters/llm/client.py` 中的 `LLMHook`。
2. 在调用前后添加观测、脱敏或审计逻辑。
3. 使用 `register_global_hook()` 全局注册。

## 安全与性能建议

- 任何路径输入都必须做工作区边界校验，防止路径穿越。
- Shell 执行默认高风险，优先白名单与超时控制。
- 不在代码中硬编码密钥，统一使用环境变量。
- 新逻辑默认考虑复杂度，优先 O(n) 线性处理，避免不必要的全量扫描。

## 变更记录

- 2026-03-12：同步文档结构与当前代码，补充 `session_memory.py`、Web/测试说明、分层职责约束。
