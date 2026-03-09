---
name: python_development_guide
description: 提供 Python 开发规范、代码风格、项目结构、测试、异常处理、日志、依赖管理与工程实践建议
---

# Python Development Guide

当用户请求与 **Python 开发规范、代码质量、工程结构、命名风格、测试、依赖管理、日志、异常处理、类型标注、性能优化** 相关的建议时，使用本 skill。

---

# 一、核心开发原则

## 1. 可读性优先

Python 代码应当 **优先保证可读性**。

- 新人应能在短时间理解代码意图
- 避免炫技写法
- 避免过度抽象

推荐：

```python
total_price = price * quantity
```

不推荐：

```python
from functools import reduce
total_price = reduce(lambda a, b: a * b, [price, quantity])
```

---

## 2. 简单优先

优先选择：

- 简单实现
- 清晰结构
- 明确逻辑

避免：

- 过度设计
- 过早优化
- 不必要的复杂框架

---

## 3. 一致性优先

同一项目应保持：

- 命名风格一致
- 日志风格一致
- 异常处理方式一致
- 项目结构一致

---

## 4. 可测试优先

业务逻辑应：

- 尽量纯函数
- 避免隐藏副作用
- 便于单元测试

推荐：

```python
def calculate_total(price: float, quantity: int) -> float:
    return price * quantity
```

不推荐：

```python
def calculate_total(order):
    price = db.get_price(order)
    return price * order.qty
```

---

# 二、命名规范

## 1. 变量命名

使用 `snake_case`

推荐：

```text
user_name
retry_count
request_timeout
```

不推荐：

```text
tmp
data1
x
```

## 2. 函数命名

函数名应表达 **动作 + 意图**

推荐：

```text
create_user
load_config
send_email
calculate_total_price
```

## 3. 类命名

使用 `PascalCase`

推荐：

```text
UserService
ConfigLoader
OrderRepository
```

## 4. 常量命名

使用全大写下划线风格

推荐：

```text
MAX_RETRY_COUNT
DEFAULT_TIMEOUT
API_BASE_URL
```

## 5. 私有成员命名

内部方法和内部属性使用前导下划线

推荐：

```text
_build_headers
_validate_payload
_internal_cache
```

---

# 三、类型标注规范

## 1. 新代码默认添加类型标注

公共函数必须标注参数和返回值。

推荐：

```python
def get_user(user_id: int) -> dict[str, str]:
    return {"id": str(user_id), "name": "Tom"}
```

不推荐：

```python
def get_user(user_id):
    return {"id": str(user_id), "name": "Tom"}
```

## 2. 优先使用明确类型

推荐：

```python
list[str]
dict[str, int]
str | None
```

## 3. 谨慎使用 Any

- `Any` 只作为过渡方案
- 核心业务结构尽量明确建模

---

# 四、函数设计规范

## 1. 单一职责

一个函数尽量只做一件事。

不推荐：

```python
def process_order(order_data):
    validate(order_data)
    amount = calculate_amount(order_data)
    save_to_db(order_data, amount)
    send_sms(order_data["user_id"])
    logger.info("order processed")
```

推荐：

```python
def process_order(order_data: dict) -> None:
    validate_order(order_data)
    amount = calculate_amount(order_data)
    save_order(order_data, amount)
    notify_user(order_data["user_id"])
```

## 2. 控制函数长度

建议：

- 普通函数尽量控制在 20~60 行
- 超长函数优先拆分
- 避免超过 3 层嵌套

## 3. 参数数量适中

参数过多时，考虑封装对象。

不推荐：

```python
create_task(name, desc, priority, timeout, retry, async_mode, notify)
```

推荐：

```python
from dataclasses import dataclass

@dataclass
class CreateTaskCommand:
    name: str
    desc: str
    priority: int
    timeout: int
    retry: int
    async_mode: bool
    notify: bool


def create_task(command: CreateTaskCommand) -> None:
    ...
```

## 4. 返回值语义清晰

不推荐：

```python
def parse_config(path):
    # 成功返回 dict，失败返回 False
    ...
```

推荐：

```python
def parse_config(path: str) -> dict:
    ...
```

失败时抛出明确异常。

---

# 五、类设计规范

## 1. 类适合封装状态和协作关系

适合用类的场景：

- 有明确状态
- 有生命周期
- 多个行为围绕同一对象展开

## 2. 避免万能类

不推荐：

```text
CommonUtil
HelperManager
AllInOneService
```

## 3. 优先依赖注入

推荐：

```python
class UserRepository:
    def get_by_id(self, user_id: int) -> dict | None:
        return None


class UserService:
    def __init__(self, user_repo: UserRepository) -> None:
        self.user_repo = user_repo
```

不推荐：

```python
class UserService:
    def __init__(self) -> None:
        self.user_repo = MysqlUserRepository()
```

---

# 六、异常处理规范

## 1. 不要裸 except

不推荐：

```python
try:
    do_something()
except:
    pass
```

推荐：

```python
try:
    do_something()
except ValueError as exc:
    logger.warning("invalid input: %s", exc)
    raise
```

## 2. 捕获具体异常

优先捕获明确异常类型，不要把所有错误都吞掉。

## 3. 不要静默失败

失败时应：

- 记录日志
- 转换成业务异常
- 或继续抛出

## 4. 自定义业务异常

推荐：

```python
class OrderNotFoundError(Exception):
    """订单不存在。"""
```

## 5. 异常信息要有上下文

推荐：

```python
raise ValueError(f"invalid user_id: {user_id}")
```

不推荐：

```python
raise ValueError("invalid")
```

---

# 七、日志规范

## 1. 使用 logging，不要到处 print

推荐：

```python
import logging

logger = logging.getLogger(__name__)
logger.info("service started")
```

## 2. 合理使用日志级别

- `DEBUG`：调试细节
- `INFO`：关键流程节点
- `WARNING`：异常但可恢复
- `ERROR`：当前操作失败
- `CRITICAL`：系统级严重故障

## 3. 日志内容要有上下文

推荐：

```python
logger.error("failed to create order, user_id=%s, order_id=%s", user_id, order_id)
```

## 4. 不记录敏感信息

禁止直接记录：

- 密码
- token
- 密钥
- 验证码
- 身份证号
- 银行卡号

---

# 八、注释与文档规范

## 1. 注释写“为什么”，少写“是什么”

不推荐：

```python
# 给 count 加 1
count += 1
```

推荐：

```python
# 第三方接口要求重试计数从 1 开始，不能从 0 开始
count += 1
```

## 2. 公共函数建议写 docstring

推荐：

```python
def load_config(path: str) -> dict:
    """
    从指定路径加载 JSON 配置文件。

    Args:
        path: 配置文件路径。

    Returns:
        解析后的配置字典。

    Raises:
        FileNotFoundError: 当文件不存在时抛出。
        ValueError: 当文件内容不是合法 JSON 时抛出。
    """
```

---

# 九、项目结构规范

推荐中小型项目结构：

```text
project/
├── app/
│   ├── __init__.py
│   ├── api/
│   ├── services/
│   ├── repositories/
│   ├── models/
│   ├── schemas/
│   ├── core/
│   └── utils/
├── tests/
├── scripts/
├── pyproject.toml
├── README.md
└── .env.example
```

## 分层建议

### api

- 路由、控制器、请求入口
- 接参数、调 service、组织响应

### services

- 业务逻辑层
- 不直接耦合 Web 框架细节

### repositories

- 数据访问层
- 封装数据库读写

### models

- 数据模型或领域对象

### schemas

- 输入输出结构定义
- 如 Pydantic 模型、DTO

### core

- 配置、日志、基础设施能力

### utils

- 通用小工具
- 不要把业务逻辑全塞进 utils

---

# 十、配置管理规范

## 1. 配置与代码分离

不要硬编码：

- 数据库地址
- API Key
- 超时时间
- 环境开关

## 2. 区分环境

建议区分：

- local
- dev
- test
- staging
- prod

## 3. 提供 .env.example

- 提供示例配置
- 不提交真实密钥
- `.env` 加入 `.gitignore`

---

# 十一、依赖管理规范

## 1. 优先使用 pyproject.toml

新项目推荐使用 `pyproject.toml` 管理依赖。

## 2. 区分生产依赖与开发依赖

生产依赖：

- 项目运行必须的包

开发依赖：

- 格式化
- lint
- 测试
- 类型检查

## 3. 控制依赖数量

- 能用标准库就不要引第三方
- 避免为小需求引入重型框架
- 定期清理未使用依赖

---

# 十二、测试规范

## 1. 优先测试核心逻辑

必须覆盖：

- 核心业务逻辑
- 边界条件
- 异常路径
- 修过 bug 的地方

## 2. 推荐使用 pytest

优点：

- 简洁
- 易读
- 生态成熟

## 3. 测试命名清晰

推荐：

```text
test_create_user_success
test_create_user_should_raise_when_email_is_invalid
```

## 4. 一个测试只验证一个核心意图

避免把多条业务路径塞进同一个测试。

## 5. 测试应可重复执行

- 不依赖执行顺序
- 不依赖外部真实服务
- 尽量使用 mock、stub、fake

---

# 十三、数据建模规范

## 1. 优先用 dataclass / Pydantic 表达结构化数据

推荐：

```python
from dataclasses import dataclass

@dataclass
class User:
    id: int
    name: str
    email: str
```

## 2. 不要到处裸传 dict

不推荐：

```python
user["name"]
user["email"]
user["age"]
```

结构稳定时，优先定义明确对象。

---

# 十四、并发与异步规范

## 1. 不要为异步而异步

适合异步的场景：

- 大量 I/O
- 网络请求并发
- 高吞吐服务

## 2. 同步与异步风格保持一致

- 一个模块尽量统一风格
- 异步函数明确使用 `async def`

## 3. 注意资源关闭

对以下资源优先使用上下文管理器：

- 文件句柄
- 数据库连接
- HTTP client
- 线程池 / 进程池

---

# 十五、安全规范

## 1. 所有外部输入都视为不可信

包括：

- HTTP 参数
- CLI 输入
- 文件内容
- 第三方接口数据

## 2. 防止命令注入

调用 shell 时：

- 尽量不用 `shell=True`
- 参数使用列表传递
- 校验用户输入

推荐：

```python
import subprocess

subprocess.run(["ls", "-l", "/tmp"], check=True)
```

不推荐：

```python
import subprocess

subprocess.run("ls -l " + user_input, shell=True)
```

## 3. 防止路径问题

- 校验文件路径
- 限制访问目录
- 避免目录穿越风险

## 4. 密钥管理

- 不把密钥写死在源码
- 不把密钥提交到仓库
- 报错和日志中不要泄漏密钥

---

# 十六、性能规范

## 1. 先保证正确，再优化

- 不要凭感觉优化
- 先测量瓶颈，再优化

## 2. 常见优化方向

- 减少重复 I/O
- 批量处理数据库操作
- 合理缓存热点结果
- 避免在循环中做高成本操作

## 3. 不要过早优化

优化不能明显损害可读性，除非收益非常明确。

---

# 十七、代码评审建议

评审重点：

1. 代码是否容易理解
2. 命名是否清晰
3. 是否有重复逻辑
4. 异常处理是否合理
5. 日志是否足够且不过量
6. 是否有测试覆盖关键路径
7. 是否引入了不必要复杂度
8. 是否存在安全和边界问题
9. 类型标注是否合理
10. 接口设计是否稳定清晰

---

# 十八、AI 生成代码约束

当 AI 生成 Python 代码时，应默认满足以下要求：

- 使用 Python 3.11+ 风格
- 公共函数添加类型标注
- 复杂函数添加 docstring
- 优先生成可直接运行、可维护的代码
- 避免无意义抽象
- 不要生成大量万能工具类
- 不要静默吞异常
- 新增依赖前先判断标准库是否足够
- 代码应方便测试

---

# 十九、推荐工程工具

## 格式化

- `black`

## lint

- `ruff`

## 类型检查

- `mypy`
- `pyright`

## 测试

- `pytest`

## 覆盖率

- `pytest-cov`

## 依赖管理

- `uv`
- `poetry`

---

# 二十、默认决策偏好

当没有额外约束时，默认采用以下偏好：

- Python 版本：**3.11+**
- 依赖管理：**pyproject.toml**
- 代码格式化：**black**
- lint：**ruff**
- 测试：**pytest**
- 类型标注：**尽量完整**
- 配置管理：**环境变量 + `.env.example`**
- 项目风格：**简单、清晰、可测试**

---

# 二十一、回答用户时的行为要求

使用本 skill 时，回答应遵循：

1. 优先给出可执行建议，不只讲概念
2. 根据用户场景控制粒度，不要一味大而全
3. 如果用户给了代码，先指出具体问题，再给改进版本
4. 如果用户在做项目，优先给项目级建议
5. 需要时给出目录结构、代码模板、测试写法
6. 对规范问题，尽量说明“为什么这样做”

---

# 二十二、不建议的做法

- 大量使用全局变量
- 滥用继承
- 滥用静态工具类
- 到处复制粘贴逻辑
- 用返回 `True/False` 代替异常表达失败原因
- 到处 print 调试而不做日志治理
- 业务逻辑与 I/O 强耦合
- 无类型、无测试、无边界处理
- 为了抽象而抽象
- 在团队项目中频繁切换风格

---

# 简短结论

好的 Python 开发规范，不是“写得最高级”，而是：

- 代码清晰
- 命名明确
- 结构稳定
- 易于测试
- 易于排错
- 易于交接
- 易于长期维护

当规范与效率冲突时，优先选择 **长期维护成本更低** 的方案。