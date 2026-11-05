from __future__ import annotations

import hashlib
import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
EVIDENCE = Path(__file__).resolve().parent
DATA = ROOT / "data"
CACHE = DATA / "research" / "r325_gold_assets"
OUTPUT = EVIDENCE / "r325_asset_probe.json"
ASSETS = {
    "518880.SH": {"code": "518880", "name": "华安黄金ETF"},
    "159937.SZ": {"code": "159937", "name": "博时黄金ETF"},
}
START = "2017-01-01"
END = "2026-10-14"


def load_r324_probe():
    path = EVIDENCE / "probe_r324_defensive_assets.py"
    spec = importlib.util.spec_from_file_location("r324_probe", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    if OUTPUT.exists() or OUTPUT.with_name(OUTPUT.name + ".sha256").exists():
        raise RuntimeError("R325 asset probe exists; refusing overwrite")
    r324 = load_r324_probe()
    r324.ASSETS = ASSETS
    r324.START = START
    r324.END = END
    CACHE.mkdir(parents=True, exist_ok=True)
    fetched_at = datetime.now(UTC).isoformat()
    frames = []
    metadata = {}
    for symbol, item in ASSETS.items():
        frame, details = r324.fetch_asset(symbol, item["code"], fetched_at)
        frames.append(frame)
        metadata[symbol] = details
        print(f"{symbol}: rows={details['rows']} missing={details['missing_calendar_days']}", flush=True)
    combined = pl.concat(frames).sort(["symbol", "date"])
    cache_path = CACHE / "gold_etf_qfq.parquet"
    if cache_path.exists():
        existing = pl.read_parquet(cache_path)
        if existing.shape != combined.shape or existing.schema != combined.schema:
            raise RuntimeError("existing R325 cache does not match the probe result")
    else:
        combined.write_parquet(cache_path, compression="zstd")
    payload = {
        "round": "R325",
        "classification": "data-availability and integrity probe only; no returns inspected",
        "source": "AkShare fund_etf_hist_em, Eastmoney daily qfq",
        "requested_window": {"start": START, "end": END},
        "fetched_at_utc": fetched_at,
        "assets": metadata,
        "cache": {
            "path": str(cache_path.relative_to(ROOT)),
            "rows": combined.height,
            "sha256": sha256(cache_path),
            "committed": False,
        },
        "decision_rule": "Both assets must have >=95% coverage of the local 000300.SH trading calendar, no duplicate dates, no null close, and no nonpositive close before performance design.",
        "all_pass": all(
            item["missing_calendar_rate"] <= 0.05
            and item["duplicate_dates"] == 0
            and item["null_close"] == 0
            and item["nonpositive_close"] == 0
            for item in metadata.values()
        ),
        "limits": [
            "This probe does not read or report any strategy return.",
            "Adjusted ETF prices are provider-reconstructed and are not an official PIT archive.",
            "The cached Parquet file is local research data and must not be committed.",
        ],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    with OUTPUT.open("xb") as stream:
        stream.write(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
