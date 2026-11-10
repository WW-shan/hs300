"""Explicit conversion from a factor-set version to the existing scoring dict."""
from __future__ import annotations

from pydantic import Field

from app.factor_research.models import FactorResearchModel, FactorSetVersion


class ScoringView(FactorResearchModel):
    set_id: str
    version: int
    content_sha256: str
    scoring: dict[str, float]
    research_only: bool = False
    excluded_roles: list[str] = Field(default_factory=list)


def factor_set_to_scoring(
    version: FactorSetVersion,
    *,
    research_only: bool = False,
) -> ScoringView:
    active_alpha = [
        member for member in version.members
        if member.role == "alpha"
        and (member.membership_status == "active" or research_only)
    ]
    if not research_only and not active_alpha:
        raise ValueError("factor set has no active alpha member")
    excluded_roles = sorted({
        member.role for member in version.members if member.role != "alpha"
    })
    if not research_only and any(
        member.role != "alpha" and member.membership_status == "active"
        for member in version.members
    ):
        raise ValueError("non-alpha active roles require a strategy-layer implementation")
    scoring: dict[str, float] = {}
    for member in active_alpha:
        if member.weight is None:
            raise ValueError(f"alpha member has no scoring weight: {member.factor_id}")
        if member.direction not in {"high", "low"}:
            raise ValueError(f"alpha member has no direction: {member.factor_id}")
        sign = 1.0 if member.direction == "high" else -1.0
        scoring[member.factor_id] = sign * member.weight
    return ScoringView(
        set_id=version.set_id,
        version=version.version,
        content_sha256=version.content_sha256,
        scoring=scoring,
        research_only=research_only,
        excluded_roles=excluded_roles,
    )
