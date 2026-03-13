from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

AgentModel = Literal["primary", "subagent"]


@dataclass(frozen=True)
class AgentDefinition:
    name: str
    model: AgentModel
    description: str


class AgentRegistry:
    def __init__(self, agents: list[AgentDefinition]):
        self._agents = {agent.name: agent for agent in agents}

    def get(self, name: str) -> AgentDefinition | None:
        normalized_name = (name or "").strip().lower()
        return self._agents.get(normalized_name)

    def list_all(self) -> list[AgentDefinition]:
        return list(self._agents.values())

    def list_subagents(self) -> list[AgentDefinition]:
        return [agent for agent in self._agents.values() if agent.model == "subagent"]


AGENT_REGISTRY = AgentRegistry(
    [
        AgentDefinition(
            name="build",
            model="primary",
            description="负责主流程实施、修改代码、执行验证，并推进任务落地。",
        ),
        AgentDefinition(
            name="plan",
            model="primary",
            description="负责规划拆解、澄清需求、沉淀执行方案，不直接承担子代理委托角色。",
        ),
        AgentDefinition(
            name="explore",
            model="subagent",
            description="适合做代码搜索、信息收集、上下文探索和独立问题排查。",
        ),
    ]
)


def get_agent(name: str) -> AgentDefinition | None:
    return AGENT_REGISTRY.get(name)


def get_all_agents() -> list[AgentDefinition]:
    return AGENT_REGISTRY.list_all()


def get_subagents() -> list[AgentDefinition]:
    return AGENT_REGISTRY.list_subagents()
