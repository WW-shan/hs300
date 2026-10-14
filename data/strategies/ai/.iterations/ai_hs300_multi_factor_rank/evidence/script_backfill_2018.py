"""Memory-safe HS300-scoped history extension BACKWARD: 2018-01-01 .. 2021-05-31.

Extends daily K + adj factor + enriched for the 300 current HS300 members and
core benchmark indices. Scoped to ~300 symbols so peak memory stays small.
"""
import json
import os
import sys
from datetime import datetime

os.environ["DATA_DIR"] = r"D:\Code\hs300\data"
sys.path.insert(0, r"D:\Code\hs300\backend")

import polars as pl  # noqa: E402

from app.tickflow.repository import DataStore, KlineRepository  # noqa: E402
from app.tickflow.policy import detect_capabilities  # noqa: E402
from app.data_providers import custom as custom_sources  # noqa: E402
from app.services import kline_sync, index_sync  # noqa: E402
from app.indicators.pipeline import run_pipeline  # noqa: E402

START = datetime(2018, 1, 1)
END = datetime(2021, 5, 31)
TODAY = datetime.now()
DATA = r"D:\Code\hs300\data"


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def main() -> None:
    store = DataStore()
    repo = KlineRepository(store)
    custom_sources.load_all()
    capset = detect_capabilities()
    log(f"data_dir={store.data_dir} caps={len(capset.all())}")

    cur = json.load(open(os.path.join(DATA, "hs300", "current_members.json"), encoding="utf-8"))
    members = [m["symbol"] if isinstance(m, dict) else m for m in cur["members"]]
    log(f"hs300 members={len(members)}")

    def on_chunk(cur_i: int, tot: int) -> None:
        if cur_i % 25 == 0 or cur_i == tot:
            log(f"  daily {cur_i}/{tot}")

    log(f"sync daily {START:%Y-%m-%d}..{END:%Y-%m-%d} for {len(members)} symbols")
    rows = kline_sync.sync_and_persist_daily_batch(
        members, repo, capset, start_date=START, end_date=END, on_chunk_done=on_chunk,
    )
    log(f"daily rows written={rows}")

    log("sync adj_factor (full history)")
    adj_rows, affected = kline_sync.sync_adj_factor(
        members, repo, capset, start_time=START, end_time=TODAY,
    )
    log(f"adj rows={adj_rows} affected={len(affected)}")

    idx = ["000001.SH", "000300.SH", "000905.SH", "399300.SZ", "000852.SH"]
    log("sync index daily")
    idx_rows = index_sync.sync_and_persist_index_daily(
        repo, capset, start_date=START, end_date=TODAY, symbols_override=idx,
    )
    log(f"index rows={idx_rows}")

    log("rebuild enriched for hs300 symbols")
    enriched_rows = run_pipeline(symbols=members)
    log(f"enriched rows={enriched_rows}")

    try:
        repo.refresh_index_views()
    except Exception as exc:  # noqa: BLE001
        log(f"refresh_index_views failed (soft): {exc}")
    log("DONE")


if __name__ == "__main__":
    main()
