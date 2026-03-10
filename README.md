# my-main-agent

一个按分层架构组织的 Python Agent 项目，当前重点是：
- 结构清晰：运行时编排、协议模型、工具实现、LLM 适配分层明确。
- 易复用：工具执行器、Hook 分发器等公共能力可复用。
- 安全可控：文件访问做工作区边界校验，命令执行有危险指令拦截。

## 目录结构

```text
src/
  main.py                     # 轻量入口（示例运行）
  agent/
    config/                   # 环境配置
      settings.py
    core/                     # 核心模型与通用能力
      context.py              # 会话上下文（ContextVar）
      message.py              # 统一 Message/Part 协议与转换
      hooks.py                # 通用 HookDispatcher
    adapters/
      llm/
        client.py             # LLM 调用适配与 LLM Hook
    runtime/
      session.py              # 会话主循环与工具调用编排
      tool_executor.py        # ToolExecutor 与 Tool Hook
      compaction.py           # 上下文压缩
    tools/
      handlers.py             # bash/read/write/edit 等工具实现
      specs.py                # 工具协议定义（BASE_TOOL/MAIN_AGENT_TOOL）
      todo_manager.py         # todo 状态管理与持久化
      todo_write.txt          # todo_write 工具描述
    skills/
      runtime.py              # skills 发现、解析、按需加载
tests/
  test_*.py                   # 核心行为回归测试
```

## 快速开始

1. 准备环境变量：
- 复制 `.env.example` 为 `.env`
- 配置 `API_KEY`

2. 运行示例：

```bash
python3 src/main.py
```

3. 运行测试：

```bash
pytest -q
```

4. 语法检查：

```bash
python3 -m py_compile $(find src -name '*.py')
```

## 如何扩展

### 1) 新增一个工具

1. 在 `src/agent/tools/handlers.py` 增加实现函数。
2. 在 `src/agent/tools/specs.py` 增加该工具的 JSON Schema 定义。
3. 在 `src/agent/runtime/session.py` 的 `_build_tool_handlers()` 注册工具名到处理函数映射。
4. 在 `tests/` 增加对应行为测试（成功路径 + 参数异常 + 安全边界）。

建议：
- 时间复杂度优先控制在 O(n) 线性处理。
- 返回值统一为字符串或可 JSON 序列化结构。
- 涉及路径、命令、外部输入时必须做防御性校验。

### 2) 新增一个 Tool Hook

1. 继承 `src/agent/runtime/tool_executor.py` 中的 `ToolHook`。
2. 实现 `before_call/after_call/on_error` 任意阶段。
3. 通过 `register_global_tool_hook()` 注册，或在 `run_session(..., tool_hooks=[...])` 局部注入。

适用场景：
- 审计日志
- 指标采集（耗时、错误率、结果大小）
- 安全策略检查

### 3) 新增一个 LLM Hook

1. 继承 `src/agent/adapters/llm/client.py` 中的 `LLMHook`。
2. 在调用前后做监控、脱敏、观测增强。
3. 使用 `register_global_hook()` 全局注册。

## 安全说明

- 文件读写通过 `safe_path` 限制在工作区内，防止路径穿越。
- `bash` 工具内置危险命令片段拦截。
- 不要在代码中硬编码密钥，统一走环境变量。

## 重构说明（2026-03-10）

本次已完成：
- 从扁平 `src/*.py` 重构为 `src/agent/*` 分层结构。
- 抽取通用 `HookDispatcher` 复用到 LLM Hook 和 Tool Hook。
- 抽取 `ToolExecutor`，使会话编排与工具执行职责分离。
- 测试导入路径全部切换到 `agent.*`，现有用例保持通过。
