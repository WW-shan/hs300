"""Pydantic contracts for the factor research layer."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ReferenceKind = Literal[
    "registered_factor",
    "virtual_scoring_field",
    "physical_column",
    "historical_definition",
    "signal_field",
    "dangling_reference",
]
Role = Literal[
    "alpha",
    "risk_filter",
    "conditioner",
    "exit_signal",
    "neutralizer",
    "control",
    "diagnostic",
]
MembershipStatus = Literal["watch", "active"]
SetState = Literal["watch", "active", "paused"]
DataClass = Literal[
    "price", "sentiment", "flow", "industry", "macro", "financial", "unknown"
]
PitStatus = Literal["verified", "declared", "not_applicable", "unknown"]
FutureDataStatus = Literal["clear", "unknown", "blocked"]
CrossSectionType = Literal[
    "stock_cross_sectional",
    "industry_cross_sectional",
    "event_sparse",
    "market_level",
    "text_or_signal",
    "unknown",
]
EvidenceVerdict = Literal[
    "unassessed", "promising", "mixed", "negative", "inconclusive"
]
Direction = Literal["high", "low", "none"]


class FactorResearchModel(BaseModel):
    """Strict immutable model used at every persistent boundary."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class SourceLocator(FactorResearchModel):
    type: Literal["builtin", "extension", "custom", "composite"]
    module: str | None = None
    catalog: str | None = None
    config_id: str | None = None
    field: str | None = None
    mode: str | None = None
    market_level: bool | None = None
    status: str | None = None
    factor_id: str | None = None
    file_sha256: str | None = None


class FactorComponent(FactorResearchModel):
    factor_id: str
    weight: float


class FactorRecord(FactorResearchModel):
    factor_id: str
    factor_version: int
    label: str
    group: str
    kind: str
    formula_text: str
    dependencies: list[str] = Field(default_factory=list)
    components: list[FactorComponent] = Field(default_factory=list)
    direction: Direction = "none"
    unit: str = "none"
    warmup_bars: int = 1
    pit_status: PitStatus = "unknown"
    pit_source: str = "none"
    future_data_status: FutureDataStatus = "unknown"
    asset_types: list[str] = Field(default_factory=list)
    scale_free: bool = True
    incremental_safe: bool = True
    definition_stability: str = "stable"
    definition_status: str = "available"
    source: SourceLocator
    data_class: DataClass = "unknown"
    cross_section_type: CrossSectionType = "unknown"
    classification_ref: str | None = None
    classification_confidence: float | None = None
    definition_fingerprint: str
    reference_kind: ReferenceKind = "registered_factor"
    tags: list[str] = Field(default_factory=list)


class PhysicalColumnRecord(FactorResearchModel):
    column_id: str
    dtype: str = "unknown"
    data_class: DataClass = "unknown"
    asset_types: list[str] = Field(default_factory=lambda: ["stock", "etf"])
    factor_eligible: bool = False
    reference_kind: ReferenceKind = "physical_column"
    source: str
    definition_fingerprint: str


class VirtualScoringFieldRecord(FactorResearchModel):
    field_id: str
    registered_factor_id: str | None = None
    dependencies: list[str] = Field(default_factory=list)
    factor_eligible: bool = False
    reference_kind: ReferenceKind = "virtual_scoring_field"
    source: str = "strategy.scoring"
    definition_fingerprint: str


class HistoricalDefinitionRecord(FactorResearchModel):
    alias_id: str
    classification: Literal["historical_definition"] = "historical_definition"
    replacement_factor_id: str | None = None
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    note: str | None = None


class DanglingReferenceRecord(FactorResearchModel):
    reference_id: str
    referenced_by: list[str] = Field(default_factory=list)
    reason: str = "unresolved reference"


class CatalogSnapshot(FactorResearchModel):
    schema_version: int = 1
    snapshot_id: str
    generated_at: str
    registry_fingerprint: str
    source_completeness: Literal["complete", "partial", "unavailable"] = "complete"
    factor_count: int
    factors: list[FactorRecord] = Field(default_factory=list)
    physical_columns: list[PhysicalColumnRecord] = Field(default_factory=list)
    virtual_scoring_fields: list[VirtualScoringFieldRecord] = Field(default_factory=list)
    historical_definitions: list[HistoricalDefinitionRecord] = Field(default_factory=list)
    dangling_references: list[DanglingReferenceRecord] = Field(default_factory=list)
    load_errors: list[str] = Field(default_factory=list)


class PoolMember(FactorResearchModel):
    factor_id: str
    factor_version: int | None = None
    definition_fingerprint: str
    reference_kind: ReferenceKind = "registered_factor"
    role: Role = "alpha"
    added_at: str
    reason: str = ""
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)


class PoolRevision(FactorResearchModel):
    schema_version: int = 1
    pool_id: str
    revision: int
    label: str
    purpose: str
    not_an_efficacy_claim: bool = True
    members: list[PoolMember] = Field(default_factory=list)
    created_at: str
    parent_revision: int | None = None
    change_reason: str = ""
    content_sha256: str


class PoolHead(FactorResearchModel):
    schema_version: int = 1
    pool_id: str
    revision: int
    revision_sha256: str
    updated_at: str
    ledger_record_id: str


class SetMember(FactorResearchModel):
    factor_id: str
    factor_version: int | None = None
    definition_fingerprint: str
    reference_kind: ReferenceKind = "registered_factor"
    role: Role = "alpha"
    membership_status: MembershipStatus = "watch"
    direction: Direction = "none"
    weight: float | None = None
    rationale: str = ""
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)


class SetPreflight(FactorResearchModel):
    reference_status: Literal["pass", "fail"] = "pass"
    role_asset_scale_status: Literal["pass", "fail"] = "pass"
    weight_status: Literal["pass", "fail"] = "pass"
    catalog_snapshot_id: str


class FactorSetVersion(FactorResearchModel):
    schema_version: int = 1
    set_id: str
    version: int
    parent_version: int | None = None
    created_at: str
    created_by: str = "research"
    change_reason: str = ""
    catalog_snapshot_id: str
    scope: dict[str, str] = Field(default_factory=dict)
    members: list[SetMember] = Field(default_factory=list)
    preflight: SetPreflight
    content_sha256: str


class SetHead(FactorResearchModel):
    schema_version: int = 1
    set_id: str
    state: SetState
    watch_version: int | None = None
    watch_version_sha256: str | None = None
    active_version: int | None = None
    active_version_sha256: str | None = None
    paused_version: int | None = None
    paused_version_sha256: str | None = None
    updated_at: str | None = None
    ledger_record_id: str | None = None


class LedgerHead(FactorResearchModel):
    schema_version: int = 1
    last_sequence: int
    last_record_hash: str
    updated_at: str | None = None


class LedgerRecord(FactorResearchModel):
    schema_version: int = 1
    sequence: int
    recorded_at: str
    actor: str
    operation: str
    set_id: str | None = None
    set_version: int | None = None
    pool_id: str | None = None
    pool_revision: int | None = None
    factor_id: str | None = None
    factor_version: int | None = None
    catalog_snapshot_id: str | None = None
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    input_fingerprints: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    verdict: str | None = None
    reason: str | None = None
    negative_knowledge: str | None = None
    supersedes_record_id: str | None = None
    ledger_prev_hash: str
    record_id: str
    record_hash: str


class Diagnostic5xPolicy(FactorResearchModel):
    hard_rejection: bool = False
    warnings: list[str] = Field(default_factory=list)


class CostPolicy(FactorResearchModel):
    baseline_multiplier: int = 2
    hard_stress_multiplier: int = 3
    diagnostic_5x: Diagnostic5xPolicy = Field(default_factory=Diagnostic5xPolicy)


class LeveragePolicy(FactorResearchModel):
    required: Literal["none"] = "none"
    max_weight_sum: float = 1.0
    min_target_weight: float = 0.0


class ResearchPolicy(FactorResearchModel):
    schema_version: int = 1
    policy_id: str = "default_v1"
    sample_sufficiency: dict[str, float | int] = Field(default_factory=dict)
    staleness_trading_days: dict[str, int] = Field(default_factory=dict)
    cost: CostPolicy = Field(default_factory=CostPolicy)
    leverage: LeveragePolicy = Field(default_factory=LeveragePolicy)
    multiple_testing: dict[str, Any] = Field(default_factory=dict)
    parameter_audit: dict[str, Any] = Field(default_factory=dict)


class ResolvedReference(FactorResearchModel):
    reference_id: str
    reference_kind: ReferenceKind
    factor_eligible: bool = False
    factor_version: int | None = None
    definition_fingerprint: str | None = None
    data_class: DataClass = "unknown"
    source: SourceLocator | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class StrategyReferenceItem(FactorResearchModel):
    reference_id: str
    referenced_by: str
    reference_kind: ReferenceKind
    factor_eligible: bool = False
    resolved: bool = True


class StrategyReferenceAudit(FactorResearchModel):
    strategies_scanned: int = 0
    references: list[StrategyReferenceItem] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    dangling: list[str] = Field(default_factory=list)


class FactorAuditRecord(FactorResearchModel):
    factor_id: str
    available: bool = False
    latest_date: str | None = None
    latest_non_null_date: str | None = None
    latest_informative_date: str | None = None
    staleness_days: int | None = None
    non_null_ratio: float = 0.0
    informative_ratio: float = 0.0
    active_date_ratio: float = 0.0
    valid_dates: int = 0
    median_non_null_symbols: float = 0.0
    median_unique_values: float = 0.0
    cross_section_type: CrossSectionType = "unknown"
    classification_confidence: float = 0.0
    classification_ref: str | None = None
    data_class: DataClass = "unknown"
    pit_status: PitStatus = "unknown"
    zero_is_missing: bool = False
    reasons: list[str] = Field(default_factory=list)


class AuditResult(FactorResearchModel):
    schema_version: int = 1
    audit_id: str
    generated_at: str
    policy_id: str
    factors: dict[str, FactorAuditRecord] = Field(default_factory=dict)
    findings: dict[str, dict[str, Any]] = Field(default_factory=dict)
