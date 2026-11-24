"""Strict, machine-checkable contract for newly registered research."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field, model_validator

from app.factor_research.models import FactorResearchModel

ResearchType = Literal[
    "project_formal",
    "statistical_screen",
    "feasibility_probe",
    "attribution",
    "derived_analysis_only",
    "synthetic_market_stress",
    "diagnostic_simulation",
    "non_return_research",
]
ConclusionLevel = Literal[
    "admitted_for_research",
    "watch_only",
    "rejected",
    "inconclusive",
    "data_gap",
    "diagnostic_only",
]
FormalEvidenceStatus = Literal["saved", "passed", "failed", "missing", "error"]

_RETURN_METRIC_KEYS = frozenset(
    {"annual_return", "total_return", "sharpe", "max_drawdown", "equity_curve"}
)
_ADMITTED_CONCLUSIONS = frozenset({"admitted_for_research", "watch_only"})


class ResearchOutputPolicy(FactorResearchModel):
    active_version: None = None
    orders_generated: Literal[False] = False
    paper_target_generated: Literal[False] = False
    production_enabled: Literal[False] = False


class ResearchExecutionContext(FactorResearchModel):
    matching: str = Field(min_length=1)
    cost_multiplier: float = Field(gt=0)
    leverage: str = Field(min_length=1)
    max_exposure_pct: float = Field(gt=0)


class SyntheticMarketSpec(FactorResearchModel):
    model: str = Field(min_length=1)
    parameters: dict[str, Any] = Field(min_length=1)
    seed: int
    path_count: int = Field(ge=2)
    benchmark: str = Field(min_length=1)


class ResearchPreregistration(FactorResearchModel):
    schema_version: Literal[1] = 1
    research_id: str = Field(min_length=1)
    research_type: ResearchType
    question: str = Field(min_length=1)
    hypothesis: str = Field(min_length=1)
    study_scope: Literal["research_only"]
    runner: str = Field(min_length=1)
    runner_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    data_source: str = Field(min_length=1)
    input_hashes: dict[str, str] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)
    sample: dict[str, Any] = Field(default_factory=dict)
    execution: ResearchExecutionContext
    synthetic_model: SyntheticMarketSpec | None = None
    gates: dict[str, Any] = Field(default_factory=dict)
    falsification_checks: list[str] = Field(default_factory=list)
    output_policy: ResearchOutputPolicy
    created_at: datetime

    @model_validator(mode="after")
    def _validate_type_contract(self):
        if self.research_type == "project_formal":
            if self.data_source != "project":
                raise ValueError("project_formal requires data_source=project")
            if self.execution.matching != "open_t+1":
                raise ValueError("project_formal requires matching=open_t+1")
            if self.execution.leverage != "none":
                raise ValueError("project_formal requires leverage=none")
            if self.execution.max_exposure_pct > 1.0:
                raise ValueError("project_formal max exposure cannot exceed 1.0")
            if self.execution.cost_multiplier != 2:
                raise ValueError("project_formal baseline cost multiplier must be 2")
        if self.research_type == "synthetic_market_stress":
            if not self.data_source.startswith("synthetic_"):
                raise ValueError("synthetic_market_stress requires synthetic_* data")
            if self.synthetic_model is None:
                raise ValueError("synthetic_market_stress requires synthetic_model")
        if self.research_type in {"attribution", "derived_analysis_only"}:
            if not self.input_hashes:
                raise ValueError(
                    "attribution and derived_analysis_only require input_hashes"
                )
        if self.research_type in {"statistical_screen", "feasibility_probe"}:
            if not self.input_hashes:
                raise ValueError(
                    "statistical_screen and feasibility_probe require input_hashes"
                )
        if self.research_type == "diagnostic_simulation":
            if not self.gates.get("known_limits"):
                raise ValueError("diagnostic_simulation requires known_limits")
        return self


class ResearchResultEnvelope(FactorResearchModel):
    schema_version: Literal[1] = 1
    research_id: str = Field(min_length=1)
    research_type: ResearchType
    runner: str = Field(min_length=1)
    runner_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    preregistration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    generated_at: datetime
    conclusion: ConclusionLevel
    gate_results: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    source_result_hashes: list[str] = Field(default_factory=list)
    formal_evidence: dict[str, str] | None = None
    known_limits: list[str] = Field(default_factory=list)
    negative_knowledge: list[str] = Field(default_factory=list)
    output_policy: ResearchOutputPolicy

    @model_validator(mode="after")
    def _validate_type_contract(self):
        if self.research_type == "project_formal":
            evidence = self.formal_evidence
            if evidence is None:
                raise ValueError("project_formal result requires formal_evidence")
            if evidence.get("persistence") != "saved":
                raise ValueError("project_formal persistence must be saved")
            if evidence.get("lookahead") != "passed":
                raise ValueError("project_formal lookahead must pass")
            if evidence.get("execution_timing") != "passed":
                raise ValueError("project_formal execution timing must pass")
        if self.research_type in {"attribution", "derived_analysis_only"}:
            if not self.source_result_hashes:
                raise ValueError(
                    "attribution and derived_analysis_only require source_result_hashes"
                )
        if self.research_type == "diagnostic_simulation":
            if not self.known_limits:
                raise ValueError("diagnostic_simulation requires known_limits")
            if self.conclusion in _ADMITTED_CONCLUSIONS:
                raise ValueError(
                    "diagnostic_simulation cannot use an admitted conclusion"
                )
        if self.research_type == "synthetic_market_stress":
            if self.conclusion in _ADMITTED_CONCLUSIONS:
                raise ValueError(
                    "synthetic_market_stress cannot use an admitted conclusion"
                )
        if self.research_type == "non_return_research":
            conflicts = set(self.metrics).intersection(_RETURN_METRIC_KEYS)
            if conflicts:
                raise ValueError(
                    f"non_return_research cannot publish return metrics: {sorted(conflicts)}"
                )
        return self


def validate_result_for_preregistration(
    preregistration: ResearchPreregistration,
    result: ResearchResultEnvelope,
    *,
    preregistration_sha256: str,
) -> list[str]:
    """Return deterministic cross-document contract errors."""
    errors: list[str] = []
    if result.preregistration_sha256 != preregistration_sha256:
        errors.append("preregistration_sha256 mismatch")
    for field in ("research_id", "research_type", "runner", "runner_sha256"):
        if getattr(result, field) != getattr(preregistration, field):
            errors.append(f"{field} mismatch")
    if result.generated_at < preregistration.created_at:
        errors.append("result generated before preregistration")
    return errors
