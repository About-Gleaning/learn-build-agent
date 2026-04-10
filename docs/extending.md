# 扩展实践指南

本文档面向想学习如何扩展本项目的开发者，重点讲扩展思路、常见入口与实践顺序。涉及必须遵守的约束、归口规则和测试基线时，以 `analyze_docs/project-context.md` 为准。

## 扩展前先判断改动属于哪一层

大多数功能都可以归到下面 4 类之一：

- 新能力：新增工具、MCP server、LSP 查询能力
- 新交互：新增 slash command、Web 展示字段、问题恢复流程
- 新角色：新增 subagent、prompt、模式切换相关能力
- 新观测：新增 Tool Hook、LLM Hook、日志字段或审计逻辑

先判断归属，再进入对应模块，可以明显减少“改一点功能，结果动了 5 个无关层”的情况。

## 常见扩展路径

### 1. 新增 Slash Command

通常需要同时理解三件事：

- 命令元信息在哪里声明
- 解析后由谁接管
- Web 侧如何自动展示

典型入口：

- `src/agent/slash_commands/registry.py`
- `src/agent/slash_commands/resolver.py`
- `src/agent/slash_commands/prompts/`

经验上，slash command 更适合承载“明确动作”，而不是自由对话别名。像 `/init`、`/analyze` 这类有清晰边界、强副作用的能力，就适合放在这里。

### 2. 新增工具

新增工具时，最重要的不是“函数写在哪”，而是先想清楚：

- 这个能力是不是应该作为工具暴露给模型
- 是否会引入安全边界问题
- 返回结果怎样结构化，才能被运行时稳定消费

典型入口：

- `src/agent/tools/`
- `src/agent/tools/specs.py`
- `src/agent/runtime/session.py`

如果工具涉及路径、Shell、网络或写入行为，要优先从已有工具中复用安全处理方式，而不是重新发明一套校验逻辑。

### 3. 新增 Subagent

Subagent 适合承载“有明显任务边界”的委派能力，例如探索、汇总、专门分析某类问题。扩展时通常需要同步思考：

- 元信息如何注册
- prompt 放在哪里
- 是否真的需要新的 agent，而不是扩展现有工具

典型入口：

- `src/agent/runtime/agents.py`
- `src/agent/runtime/prompts/`
- `src/agent/runtime/session.py`

### 4. 调整 Web 输出

Web 变更最容易犯的错是“后端字段哪里方便就在哪里拼”。更稳妥的做法是：

- 在 schema 中定义清楚输入输出形状
- 在 serializer 中集中做映射
- 在 app 层只保留路由和响应封装

典型入口：

- `src/agent/web/schemas.py`
- `src/agent/web/serializers.py`
- `src/agent/web/app.py`

## 什么时候该读开发主手册

当你要真正开始改代码，而不是只是理解思路时，请切换到 `analyze_docs/project-context.md`。尤其在以下场景，不建议只看本文件：

- 需要确认某个模块是不是唯一归口
- 需要知道测试最低要求
- 需要判断哪些改动会触碰运行时红线
- 需要补全文档或更新规范

## 关于 `/analyze`

`/analyze` 的职责是初始化第一版 `analyze_docs/project-context.md`。

- 如果该文件不存在，命令会生成首版开发手册
- 如果该文件已存在，命令应直接停止
- 如果工作区内存在多项目或多模块结构，命令需要先识别模块边界、依赖关系、公共模块职责与启动入口，再写入开发手册
- 后续维护以人工更新该文档为主，不再依赖命令覆盖

这项设计的目的，是保护人工沉淀下来的开发知识不被“重新生成”冲掉。
