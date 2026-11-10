from __future__ import annotations

from pathlib import Path

from app.factor_research.bootstrap import bootstrap_core_research


def test_core_seed_is_watch_only(tmp_path: Path) -> None:
    result = bootstrap_core_research(
        tmp_path,
        core_members=[{
            "factor_id": "momentum_20d",
            "role": "alpha",
            "membership_status": "watch",
            "direction": "high",
            "weight": 1.0,
        }],
    )
    assert result.set_id == "core_v1"
    assert result.state == "watch"
    assert result.active_version is None
    assert result.snapshot_id.startswith("sha256:")
