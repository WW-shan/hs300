from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import akshare as ak
import polars as pl

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
EVIDENCE = Path(__file__).resolve().parent
DATA = ROOT / "data"
CACHE = DATA / "research" / "r193_treasury"
OUTPUT = EVIDENCE / "r331_treasury_futures_restore.json"
SYMBOLS = ["T0", "TF0", "TS0", "TL0"]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    if OUTPUT.exists() or OUTPUT.with_name(OUTPUT.name + ".sha256").exists():
        raise RuntimeError("R331 treasury futures restore manifest exists; refusing overwrite")
    CACHE.mkdir(parents=True, exist_ok=True)
    series = {}
    for symbol in SYMBOLS:
        path = CACHE / f"{symbol}.parquet"
        if not path.exists():
            raw = ak.futures_main_sina(symbol=symbol)
            frame = (
                pl.from_pandas(raw)
                .rename(
                    {
                        "日期": "date",
                        "开盘价": "open",
                        "最高价": "high",
                        "最低价": "low",
                        "收盘价": "close",
                        "成交量": "volume",
                        "持仓量": "open_interest",
                        "动态结算价": "settle",
                    }
                )
                .with_columns(pl.col("date").cast(pl.Date))
                .sort("date")
            )
            frame.write_parquet(path)
        frame = pl.read_parquet(path)
        series[symbol] = {
            "rows": frame.height,
            "start": str(frame["date"].min()),
            "end": str(frame["date"].max()),
            "close_nulls": frame["close"].null_count(),
            "close_zeros": frame.filter(pl.col("close") == 0).height,
            "sha256": sha256(path),
        }
        print(symbol, series[symbol], flush=True)
    required = all(series[symbol]["end"] >= "2026-11-05" for symbol in ("T0", "TF0"))
    payload = {
        "round": "R331",
        "classification": "research dependency restoration; no returns inspected",
        "purpose": "Restore the missing R193 treasury-futures cache required by build_alt_factor_data.py.",
        "source": "AkShare futures_main_sina",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "series": series,
        "required_T0_TF0_through_2026-11-05": required,
        "cache_path": str(CACHE.relative_to(ROOT)),
        "limits": [
            "This restore recreates a previously generated local research cache; it is not a new return test.",
            "The existing r193_treasury_probe.json is preserved and was not overwritten.",
        ],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    with OUTPUT.open("xb") as stream:
        stream.write(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii"
    )
    print(json.dumps({"required": required, "output": str(OUTPUT.relative_to(ROOT))}, ensure_ascii=False, indent=2))
    return 0 if required else 1


if __name__ == "__main__":
    raise SystemExit(main())
