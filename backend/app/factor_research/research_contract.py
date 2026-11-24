"""Strict, machine-checkable contract for newly registered research."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
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


@dataclass(frozen=True)
class PublishedResearch:
    result_path: Path
    markdown_path: Path
    result_sha256: str
    markdown_sha256: str
    ledger_record_id: str
    created: bool


def _sha256_bytes(raw: bytes) -> str:
    import hashlib

    return hashlib.sha256(raw).hexdigest()


def _read_verified_sidecar(path: Path) -> tuple[str, bytes]:
    sidecar = path.with_name(path.name + ".sha256")
    if not path.is_file() or not sidecar.is_file():
        raise FileNotFoundError(f"missing research artifact or sidecar: {path.name}")
    parts = sidecar.read_text(encoding="ascii").strip().split()
    if len(parts) != 2 or parts[1] != path.name:
        raise ValueError(f"malformed research sidecar: {sidecar.name}")
    raw = path.read_bytes()
    if parts[0] != _sha256_bytes(raw):
        raise ValueError(f"research sidecar hash mismatch: {path.name}")
    return parts[0], raw


def _result_targets(preregistration_path: Path) -> tuple[Path, Path]:
    name = preregistration_path.name
    if not name.endswith("_preregistration.json"):
        raise ValueError("preregistration filename must end with _preregistration.json")
    result_name = name.removesuffix("_preregistration.json") + "_result.json"
    return (
        preregistration_path.with_name(result_name),
        preregistration_path.with_name(result_name.removesuffix(".json") + ".md"),
    )


def publish_research_artifacts(
    data_dir: Path,
    *,
    preregistration_path: Path,
    result: ResearchResultEnvelope,
    markdown: str,
    operation: str,
) -> PublishedResearch:
    """Publish immutable JSON/Markdown evidence and bind it to the ledger."""
    import json
    import os

    from app.factor_research.automation import bind_research_evidence

    data_dir = Path(data_dir)
    prereg_hash, prereg_raw = _read_verified_sidecar(preregistration_path)
    prereg = ResearchPreregistration.model_validate_json(prereg_raw)
    errors = validate_result_for_preregistration(
        prereg, result, preregistration_sha256=prereg_hash
    )
    runner_path = data_dir / prereg.runner
    if not runner_path.is_file():
        raise ValueError(f"research runner does not exist: {prereg.runner}")
    if _sha256_bytes(runner_path.read_bytes()) != prereg.runner_sha256:
        raise ValueError("research runner hash no longer matches preregistration")
    if not isinstance(markdown, str) or not markdown.strip():
        raise ValueError("research Markdown report must not be empty")

    result_path, markdown_path = _result_targets(preregistration_path)
    result_raw = (
        json.dumps(
            result.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")
    markdown_raw = markdown.encode("utf-8")
    result_hash = _sha256_bytes(result_raw)
    markdown_hash = _sha256_bytes(markdown_raw)

    paths = [
        result_path,
        result_path.with_name(result_path.name + ".sha256"),
        markdown_path,
        markdown_path.with_name(markdown_path.name + ".sha256"),
    ]
    existing = [path for path in paths if path.exists()]
    if existing:
        if len(existing) != len(paths):
            raise FileExistsError("partial research publication is not reusable")
        stored_result_hash, stored_result_raw = _read_verified_sidecar(result_path)
        stored_markdown_hash, stored_markdown_raw = _read_verified_sidecar(markdown_path)
        if stored_result_raw != result_raw or stored_markdown_raw != markdown_raw:
            raise ValueError("immutable research artifact bytes changed")
        record, _created = bind_research_evidence(
            data_dir,
            operation=operation,
            evidence_paths=[result_path, markdown_path],
            research_id=prereg.research_id,
            payload={
                "verdict": result.conclusion,
                "reason": "; ".join(errors),
                "input_fingerprints": {
                    "research_type": prereg.research_type,
                    "preregistration_sha256": prereg_hash,
                    "runner_sha256": prereg.runner_sha256,
                    "result_sha256": stored_result_hash,
                    "markdown_sha256": stored_markdown_hash,
                },
                "metrics": result.metrics,
            },
        )
        return PublishedResearch(
            result_path=result_path,
            markdown_path=markdown_path,
            result_sha256=stored_result_hash,
            markdown_sha256=stored_markdown_hash,
            ledger_record_id=record.record_id,
            created=False,
        )
    if result_path.exists() or markdown_path.exists():
        raise FileExistsError(f"research result already exists: {result_path.name}")

    def atomic_create(path: Path, raw: bytes) -> None:
        tmp = path.with_name(f".{path.name}.tmp")
        if tmp.exists():
            tmp.unlink()
        with tmp.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(tmp, path)
        finally:
            tmp.unlink()

    created_paths: list[Path] = []
    try:
        atomic_create(result_path, result_raw)
        created_paths.append(result_path)
        atomic_create(
            result_path.with_name(result_path.name + ".sha256"),
            f"{result_hash}  {result_path.name}\n".encode("ascii"),
        )
        created_paths.append(result_path.with_name(result_path.name + ".sha256"))
        atomic_create(markdown_path, markdown_raw)
        created_paths.append(markdown_path)
        atomic_create(
            markdown_path.with_name(markdown_path.name + ".sha256"),
            f"{markdown_hash}  {markdown_path.name}\n".encode("ascii"),
        )
        created_paths.append(markdown_path.with_name(markdown_path.name + ".sha256"))
        record, _created = bind_research_evidence(
            data_dir,
            operation=operation,
            evidence_paths=[result_path, markdown_path],
            research_id=prereg.research_id,
            payload={
                "verdict": result.conclusion,
                "input_fingerprints": {
                    "research_type": prereg.research_type,
                    "preregistration_sha256": prereg_hash,
                    "runner_sha256": prereg.runner_sha256,
                    "result_sha256": result_hash,
                    "markdown_sha256": markdown_hash,
                },
                "metrics": result.metrics,
            },
        )
    except Exception:
        for path in reversed(created_paths):
            if path.exists():
                path.unlink()
        raise
    return PublishedResearch(
        result_path=result_path,
        markdown_path=markdown_path,
        result_sha256=result_hash,
        markdown_sha256=markdown_hash,
        ledger_record_id=record.record_id,
        created=True,
    )
