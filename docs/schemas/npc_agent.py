"""NPC Agent state and I/O schema. See ../06_NPC_Agent_Spec.md for design rationale."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class NPCPersona(BaseModel):
    """相对固定的角色人格，不轻易被单次对话改变。"""

    traits: dict[str, float] = Field(
        default_factory=dict, description="e.g. {'honest': 0.8, 'ambitious': 0.6}"
    )
    background: str = ""


class NPCGoal(BaseModel):
    primary: str
    secondary: list[str] = Field(default_factory=list)


class NPCState(BaseModel):
    """NPC 的 Agent 内部状态。与 world_state.NPCWorldState 分开：
    这里是"这个 NPC 怎么想"，world_state 里是"这个 NPC 客观上在哪/是否存活"。"""

    npc_id: str
    persona: NPCPersona
    goal: NPCGoal
    emotion: str = "neutral"
    beliefs: dict[str, Any] = Field(
        default_factory=dict, description="e.g. {'player_is_suspicious': True}"
    )


class ToolCall(BaseModel):
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: Any = None


class AgentPlan(BaseModel):
    """Planning 阶段输出。通常与 dialogue 合并为一次 LLM 调用的结构化输出。"""

    reasoning: str = Field(description="内部推理，如 '直接回答会破坏信任，应转移话题'")
    strategy: str = Field(description="e.g. 'avoid_direct_answer'")
    tool_calls: list[ToolCall] = Field(default_factory=list)


class DialogueGenerationInput(BaseModel):
    """Dialogue Generation 阶段的输入：把结构化状态/剧情事件译成符合人设的台词。
    见 ../06_NPC_Agent_Spec.md#3-dialogue-generation。"""

    persona: NPCPersona
    emotion: str
    plan: AgentPlan
    narrative_event: dict[str, Any] | None = Field(
        default=None,
        description="Narrative Engine 生成的结构化事件内容（若本轮有触发），"
        "如 {'who_betrays': 'NPC_B', 'how': '...', 'reveal_timing': 'next_chapter'}。"
        "对应 narrative_event.NarrativeEvent.generated_content。",
    )


class NPCAgentResponse(BaseModel):
    """一次交互的完整输出，用于写回 AgentTrace 和返回给客户端。"""

    npc_id: str
    plan: AgentPlan
    dialogue: str = Field(description="Dialogue Generation 阶段的最终输出")
    action_proposal_id: str | None = Field(
        default=None, description="如果本次响应涉及世界状态变更，关联的 ActionProposal id"
    )
    timestamp: datetime = Field(default_factory=datetime.utcnow)
