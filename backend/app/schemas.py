
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class InvestigationRequest(BaseModel):
    alert_id: str = Field(min_length=1, max_length=64)

    @field_validator("alert_id", mode="before")
    @classmethod
    def trim_alert_id(cls, value):
        return value.strip() if isinstance(value, str) else value


class GraphNode(BaseModel):
    id: str
    label: str
    type: str


class GraphEdge(BaseModel):
    source: str
    target: str
    type: str
    properties: dict[str, Any] = Field(default_factory=dict)


class GraphPath(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]


class ProposedAction(BaseModel):
    action_id: str
    action_type: Literal[
        "REVOKE_SESSION",
        "ISOLATE_WORKLOAD",
    ]
    target_id: str
    title: str
    reason: str
    status: Literal["PROPOSED"]


class InvestigationResponse(BaseModel):
    investigation_id: str
    alert_id: str
    target: dict[str, str]
    identity_id: str | None = None

    outcome: Literal[
        "path_found",
        "no_path_in_graph",
        "unknown_entity",
        "invalid_input",
        "query_error",
    ]

    risk_score: int | None = Field(default=None, ge=0, le=100)
    severity: Literal[
        "LOW",
        "MEDIUM",
        "HIGH",
        "CRITICAL",
        "UNASSESSED",
    ] = "UNASSESSED"

    summary: str
    paths: list[GraphPath]
    evidence: list[str]
    proposed_actions: list[ProposedAction]
    warnings: list[str] = Field(default_factory=list)

    # Makes it clear that this result is synthetic.
    data_source: Literal["synthetic_falkordb"] = "synthetic_falkordb"
