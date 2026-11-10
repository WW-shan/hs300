from __future__ import annotations

from pathlib import Path

from app.factor_research.catalog import (
    build_catalog,
    load_catalog,
    load_latest_catalog,
    save_catalog,
)


def test_catalog_contains_registry_and_physical_columns(tmp_path: Path, monkeypatch) -> None:
    from app import config as app_config

    monkeypatch.setattr(app_config.settings, "data_dir", tmp_path)
    snapshot = build_catalog(tmp_path, generated_at="2026-11-10T00:00:00Z")
    assert snapshot.factor_count >= 81
    assert any(item.factor_id == "momentum_20d" for item in snapshot.factors)
    assert any(column.column_id == "amount" for column in snapshot.physical_columns)
    assert any(column.column_id == "consecutive_limit_ups" for column in snapshot.physical_columns)
    assert snapshot.snapshot_id.startswith("sha256:")
    assert snapshot.source_completeness == "complete"


def test_catalog_snapshot_roundtrip(tmp_path: Path, monkeypatch) -> None:
    from app import config as app_config

    monkeypatch.setattr(app_config.settings, "data_dir", tmp_path)
    snapshot = build_catalog(tmp_path, generated_at="2026-11-10T00:00:00Z")
    path = save_catalog(tmp_path, snapshot)
    assert path.exists()
    assert load_catalog(tmp_path, snapshot.snapshot_id) == snapshot
    assert load_latest_catalog(tmp_path) == snapshot


def test_catalog_fingerprints_are_stable(tmp_path: Path, monkeypatch) -> None:
    from app import config as app_config

    monkeypatch.setattr(app_config.settings, "data_dir", tmp_path)
    first = build_catalog(tmp_path, generated_at="2026-11-10T00:00:00Z")
    second = build_catalog(tmp_path, generated_at="2026-11-11T00:00:00Z")
    assert first.snapshot_id == second.snapshot_id
    assert first.registry_fingerprint == second.registry_fingerprint


def test_latest_pointer_can_be_rebuilt_after_delete(tmp_path: Path, monkeypatch) -> None:
    from app import config as app_config

    monkeypatch.setattr(app_config.settings, "data_dir", tmp_path)
    snapshot = build_catalog(tmp_path, generated_at="2026-11-10T00:00:00Z")
    save_catalog(tmp_path, snapshot)
    latest = tmp_path / "factor_research" / "catalog" / "latest.json"
    latest.unlink()
    save_catalog(tmp_path, snapshot)
    assert latest.exists()
