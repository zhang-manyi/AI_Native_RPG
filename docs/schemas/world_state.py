"""World State schema. Single Source of Truth for the game world.
See ../04_World_State_Manager.md for design rationale."""

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Visibility(str, Enum):
    HIDDEN = "hidden"
    PARTIAL = "partial"
    REVEALED = "revealed"


class Fact(BaseModel):
    """一条世界事实，可能对玩家不可见。"""

    fact_id: str
    value: Any
    visibility: Visibility = Visibility.REVEALED
    reveal_condition: str | None = Field(
        default=None,
        description="e.g. 'player.trust[NPC_A] > 60 OR quest.investigation.stage >= 3'",
    )
    partial_value: Any | None = Field(
        default=None,
        description="visibility=partial 时展示给玩家的部分信息，如 '有个嫌疑人' 而非具体身份",
    )


class NPCWorldState(BaseModel):
    """NPC 在世界状态中的客观数据（不含 persona/goal/emotion 等 Agent 内部状态，
    那些定义在 npc_agent.py 里）。

    注意：关系值不在这里，在 WorldState.relationships，因为它是 (npc, target) 二元
    关系而非 NPC 单体属性，且需要被 Validator/Narrative Engine/Agent 三方读取。
    见 ../04_World_State_Manager.md#21-状态归属边界客观事实-vs-主观认知。"""

    npc_id: str
    alive: bool = True
    location: str
    faction_id: str | None = None


class RelationshipState(BaseModel):
    """角色间的量化关系。事实源在 World State，memory.RelationshipMemory 是其只读投影。"""

    trust: float = Field(default=0.0, ge=-100, le=100)
    fear: float = Field(default=0.0, ge=-100, le=100)
    respect: float = Field(default=0.0, ge=-100, le=100)


class FactionState(BaseModel):
    faction_id: str
    power: float = 0.5
    stability: float = 0.5
    relationships: dict[str, float] = Field(
        default_factory=dict, description="faction_id -> relationship score [-1, 1]"
    )


class QuestState(BaseModel):
    quest_id: str
    stage: int = 0
    status: Literal["not_started", "active", "completed", "failed"] = "not_started"


class WorldState(BaseModel):
    """Reality Layer. 全量事实，唯一事实源。"""

    world_id: str
    time_day: int = 0

    npcs: dict[str, NPCWorldState] = Field(default_factory=dict)
    factions: dict[str, FactionState] = Field(default_factory=dict)
    quests: dict[str, QuestState] = Field(default_factory=dict)
    facts: dict[str, Fact] = Field(default_factory=dict)

    last_updated: datetime = Field(default_factory=datetime.utcnow)


class VisibleState(BaseModel):
    """PlayerView() 的输出：某个玩家当前能看到的世界子集。"""

    player_id: str
    time_day: int
    visible_facts: dict[str, Any] = Field(
        default_factory=dict, description="fact_id -> value (or partial_value)"
    )
    known_npc_locations: dict[str, str] = Field(default_factory=dict)


class ActionProposal(BaseModel):
    """Agent 提交的世界变更请求，不能直接写入 WorldState。"""

    proposal_id: str
    actor_id: str
    action_type: str
    target_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class ActionValidationResult(BaseModel):
    proposal_id: str
    approved: bool
    reason: str | None = None
    applied_changes: dict[str, Any] | None = None
