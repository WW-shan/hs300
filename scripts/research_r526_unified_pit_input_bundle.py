#!/usr/bin/env python3
"""R526: freeze small PIT lineage inputs for a later unified rerun."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
E = ROOT / "data" / "factor_research" / "research"
PRE = E / "r526_unified_pit_input_bundle_preregistration.json"
BUNDLE = E / "r526_unified_pit_bundle_window"
MANIFEST = E / "r526_unified_pit_bundle_manifest_2026-11-23.json"
RESULT = E / "r526_unified_pit_bundle_manifest_2026-11-23.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    for path in (MANIFEST, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R526 output exists; refusing overwrite: {path.name}")
    if sha(PRE) != PRE.with_name(PRE.name + ".sha256").read_text().split()[0]:
        raise RuntimeError("R526 preregistration checksum mismatch")
    prereg = json.loads(PRE.read_text(encoding="utf-8"))
    if BUNDLE.exists():
        expected_names = {Path(relative).name for relative in prereg["inputs"].values()}
        actual_names = {path.name for path in BUNDLE.iterdir() if path.is_file()}
        if not expected_names <= actual_names:
            raise RuntimeError("R526 bundle exists but is incomplete")
    else:
        BUNDLE.mkdir(parents=True)
        for relative in prereg["inputs"].values():
            shutil.copy2(ROOT / relative, BUNDLE / Path(relative).name)
    files = []
    for relative in prereg["inputs"].values():
        name = Path(relative).name
        path = BUNDLE / name
        files.append({"name": name, "bytes": path.stat().st_size, "sha256": sha(path)})
    membership = pl.read_parquet(BUNDLE / "daily_snapshots.parquet")
    industry = pl.read_parquet(BUNDLE / "industry_history.parquet")
    extension = pl.read_parquet(BUNDLE / "alt_hs300_required_fields.parquet")
    window = membership.filter(
        (pl.col("query_date") >= pl.date(2022, 5, 5))
        & (pl.col("query_date") <= pl.date(2026, 11, 5))
    )
    counts = window.group_by("query_date").agg(pl.len().alias("n"))
    checks = {
        "membership_sessions": counts.height == prereg["gates"]["membership_sessions"],
        "membership_symbols_per_session": (
            counts["n"].min() == counts["n"].max() == prereg["gates"]["membership_symbols_per_session"]
        ),
        "industry_effective_date_min": str(industry["effective_date"].min())
        == prereg["gates"]["industry_effective_date_min"],
        "extension_rows": extension.height == prereg["gates"]["extension_rows"],
        "bundle_size_under_mb": sum(item["bytes"] for item in files)
        < prereg["gates"]["bundle_size_under_mb"] * 1024 * 1024,
        "all_hashes_match": all(
            sha(BUNDLE / Path(relative).name)
            == hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
            for relative in prereg["inputs"].values()
        ),
    }
    payload = {
        "round": "R526",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PRE),
        "source_round": prereg["source_round"],
        "bundle_directory": BUNDLE.relative_to(ROOT).as_posix(),
        "files": files,
        "windows": {"start": "2022-05-05", "end": "2026-11-05"},
        "audit": {
            "membership_sessions": counts.height,
            "membership_rows": window.height,
            "industry_rows": industry.height,
            "extension_rows": extension.height,
            "extension_symbols": extension["symbol"].n_unique(),
        },
        "gate": {"pass": all(checks.values()), "checks": checks},
        "decision": {**prereg["output_policy"]},
        "known_limits": prereg["known_limits"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    MANIFEST.write_bytes(raw)
    MANIFEST.with_name(MANIFEST.name + ".sha256").write_text(
        f"{sha(MANIFEST)}  {MANIFEST.name}\n", encoding="ascii"
    )
    lines = [
        "# R526 统一 PIT 输入冻结包",
        "",
        f"- 包目录：`{payload['bundle_directory']}`",
        f"- 文件数：{len(files)}",
        f"- 总字节：{sum(item['bytes'] for item in files)}",
        f"- 名单交易日：{counts.height}（每日 300 只）",
        f"- 行业历史：{industry.height} 行，最早生效日 `{industry['effective_date'].min()}`",
        f"- 扩展表：{extension.height} 行，{extension['symbol'].n_unique()} 个符号",
        "",
        "## 门槛",
        "",
    ]
    lines += [f"- {name}: {'通过' if value else '未过'}" for name, value in checks.items()]
    lines += ["", "## 边界", ""] + [f"- {item}" for item in prereg["known_limits"]] + [""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(
        f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii"
    )
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
