# agents/base.py
from typing import Dict, Any, Callable
from enum import Enum


class AgentStatus(Enum):
    SUCCESS = "success"
    FAILED  = "failed"
    RETRY   = "retry"


class AgentResponse:
    def __init__(self, status: AgentStatus, output=None, error=None, metadata=None):
        self.status   = status
        self.output   = output   or {}
        self.error    = error
        self.metadata = metadata or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status":   self.status.value,
            "output":   self.output,
            "error":    str(self.error) if self.error else None,
            "metadata": self.metadata,
        }


class Agent:
    """
    Base class every concrete agent must extend.
    Subclasses declare:
        name   : str                  – unique human-readable name
        skills : Dict[str, Callable]  – maps skill_id → callable(params) → AgentResponse
    """
    name:   str
    skills: Dict[str, Callable]

    def get_skills(self) -> Dict[str, Callable]:
        return self.skills

    def run(self, skill_id: str, params: Dict[str, Any]) -> AgentResponse:
        skills = self.get_skills()
        if skill_id not in skills:
            return AgentResponse(
                status=AgentStatus.FAILED,
                error=f"Skill '{skill_id}' not found in agent '{self.name}'",
            )
        try:
            return skills[skill_id](params)
        except Exception as exc:
            return AgentResponse(
                status=AgentStatus.FAILED,
                error=str(exc),
                metadata={"agent": self.name, "skill": skill_id},
            )