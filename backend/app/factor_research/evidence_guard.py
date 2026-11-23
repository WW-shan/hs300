"""Shared guard for research runners that produce portfolio return claims."""
from __future__ import annotations

from typing import Any


def require_formal_evidence(result: Any, *, label: str) -> dict[str, Any]:
    """Fail closed unless the project recorded a reproducible, clean backtest."""
    evidence = getattr(result, "evidence", None)
    if not isinstance(evidence, dict):
        raise RuntimeError(f"{label}: missing formal evidence")
    persistence = evidence.get("persistence")
    lookahead = evidence.get("lookahead")
    execution = evidence.get("execution_timing")
    if not isinstance(persistence, dict) or persistence.get("status") != "saved":
        raise RuntimeError(f"{label}: formal evidence not saved")
    if not isinstance(lookahead, dict) or lookahead.get("status") != "passed":
        raise RuntimeError(f"{label}: lookahead audit did not pass")
    if not isinstance(execution, dict) or execution.get("status") != "passed":
        raise RuntimeError(f"{label}: execution timing audit did not pass")
    # Return the original evidence mapping, augmented with an explicit formal
    # summary. Callers need the complete evidence structure for provenance,
    # while the summary states the three mandatory pass conditions.
    return {
        **evidence,
        "formal_status": {
            "run_id": result.run_id,
            "persistence": persistence.get("status"),
            "lookahead": lookahead.get("status"),
            "execution_timing": execution.get("status"),
        },
    }
