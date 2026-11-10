"""Strategy payload reference audit built on the exact resolver."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from app.factor_research.models import StrategyReferenceAudit, StrategyReferenceItem
from app.factor_research.references import ReferenceResolver

_REFERENCE_CONTAINERS = frozenset({"scoring", "members"})


def _collect_payload_references(value: Any, owner: str, out: set[tuple[str, str]], *, parent_key: str | None = None) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key)
            if key_text in _REFERENCE_CONTAINERS and isinstance(child, Mapping):
                for reference_id in child:
                    out.add((str(reference_id), owner))
            if key_text == "overlay_filter":
                _collect_overlay_references(child, owner, out)
            if key_text == "factor" and isinstance(child, str):
                out.add((child, owner))
            _collect_payload_references(child, owner, out, parent_key=key_text)
    elif isinstance(value, list):
        for child in value:
            _collect_payload_references(child, owner, out, parent_key=parent_key)


def _collect_overlay_references(value: Any, owner: str, out: set[tuple[str, str]]) -> None:
    if isinstance(value, Mapping):
        field = value.get("field")
        if isinstance(field, str):
            out.add((field, owner))
        for child in value.values():
            _collect_overlay_references(child, owner, out)
    elif isinstance(value, list):
        for child in value:
            _collect_overlay_references(child, owner, out)


def audit_strategy_payloads(
    payloads: Iterable[Mapping[str, Any]],
    resolver: ReferenceResolver,
) -> StrategyReferenceAudit:
    pairs: set[tuple[str, str]] = set()
    strategy_count = 0
    for payload in payloads:
        strategy_count += 1
        owner = str(payload.get("id", payload.get("strategy_id", f"strategy:{strategy_count}")))
        _collect_payload_references(payload, owner, pairs)

    items: list[StrategyReferenceItem] = []
    counts: dict[str, int] = {}
    dangling: set[str] = set()
    for reference_id, owner in sorted(pairs):
        resolved = resolver.resolve(reference_id)
        counts[resolved.reference_kind] = counts.get(resolved.reference_kind, 0) + 1
        if resolved.reference_kind == "dangling_reference":
            dangling.add(reference_id)
        items.append(StrategyReferenceItem(
            reference_id=reference_id,
            referenced_by=owner,
            reference_kind=resolved.reference_kind,
            factor_eligible=resolved.factor_eligible,
            resolved=resolved.reference_kind != "dangling_reference",
        ))
    return StrategyReferenceAudit(
        strategies_scanned=strategy_count,
        references=items,
        counts=counts,
        dangling=sorted(dangling),
    )


def load_strategy_payloads(data_dir: Path) -> list[Mapping[str, Any]]:
    """Load top-level strategy JSON files for a best-effort offline audit."""
    import json

    root = Path(data_dir) / "strategies"
    if not root.exists():
        return []
    out: list[Mapping[str, Any]] = []
    for path in sorted(root.rglob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, Mapping):
            item = dict(payload)
            item.setdefault("id", path.stem)
            out.append(item)
    return out
