from __future__ import annotations

from pathlib import Path

from app.factor_research.parameter_audit import build_parameter_audit_summary


def test_parameter_audit_covers_every_registry_family(tmp_path: Path) -> None:
    summary = build_parameter_audit_summary(
        data_dir=Path(__file__).resolve().parents[3] / "data",
    )

    assert summary["registry_family_count"] == 62
    assert summary["covered_family_count"] == 62
    assert summary["missing_family_ids"] == []
    assert len(summary["families"]) == 62


def test_parameter_audit_marks_recommendations_and_current_members() -> None:
    summary = build_parameter_audit_summary(
        data_dir=Path(__file__).resolve().parents[3] / "data",
    )
    by_id = {row["family_id"]: row for row in summary["families"]}

    assert by_id["idio_vol"]["recommended_parameter"] == 20
    assert by_id["idio_vol"]["current_parameter"] == 60
    assert by_id["idio_vol"]["current_set_member"] == "ext_alt_hs300_idio_vol_40d_low"
    assert by_id["vol_of_vol"]["recommended_parameter"] == 20
    assert by_id["market_down_beta"]["recommended_parameter"] is None
    assert by_id["market_down_beta"]["recommendation_status"] == "rejected"
