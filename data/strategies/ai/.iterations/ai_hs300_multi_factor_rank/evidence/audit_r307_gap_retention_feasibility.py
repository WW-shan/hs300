from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[6]
DATA = ROOT / "data"
EVIDENCE = Path(__file__).resolve().parent
PREREG = EVIDENCE / "r307_gap_retention_preregistration.json"
MEMBERSHIP = DATA / "research" / "r78_daily_membership" / "daily_snapshots.parquet"
ENRICHED = DATA / "kline_daily_enriched"
MANIFEST = EVIDENCE / "r307_input_manifest.json"
OUT = EVIDENCE / "r307_gap_retention_feasibility.json"


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_once(path: Path, payload: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(payload)


def file_record(path: Path) -> dict:
    stat = path.stat()
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": sha(path),
    }


def split_name(day: date, prereg: dict) -> str | None:
    for name, bounds in prereg["design"]["splits"].items():
        start, end = (date.fromisoformat(value) for value in bounds)
        if start <= day <= end:
            return name
    return None


def main() -> None:
    if MANIFEST.exists() or OUT.exists():
        raise RuntimeError("R307 feasibility outputs already exist; refusing overwrite")
    prereg_raw = PREREG.read_bytes()
    prereg_hash = hashlib.sha256(prereg_raw).hexdigest()
    sidecar_hash = PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]
    if prereg_hash != sidecar_hash:
        raise RuntimeError("R307 preregistration checksum mismatch")
    prereg = json.loads(prereg_raw)

    membership_raw = pl.read_parquet(
        MEMBERSHIP, columns=["query_date", "update_date", "symbol"]
    )
    late_membership = membership_raw.filter(pl.col("update_date") > pl.col("query_date"))
    if late_membership.height:
        raise RuntimeError("R78 contains membership rows updated after query_date; stop before feasibility calculation")
    membership = membership_raw.select(["query_date", "symbol"]).unique(
        subset=["query_date", "symbol"]
    )
    calendar = sorted(membership["query_date"].unique().to_list())
    calendar_index = {day: index for index, day in enumerate(calendar)}
    first = date.fromisoformat(prereg["design"]["splits"]["discovery"][0])
    last = date.fromisoformat(prereg["design"]["splits"]["holdout"][1])
    first_i = next((i for i, day in enumerate(calendar) if day >= first), None)
    if first_i is None:
        raise RuntimeError("R78 calendar does not cover R307 start")
    signal_dates = [
        day for i, day in enumerate(calendar)
        if i >= first_i and (i - first_i) % 5 == 0 and day <= last and split_name(day, prereg)
    ]
    if not signal_dates:
        raise RuntimeError("R78 calendar has no sampled dates in frozen split windows")
    signal_set = set(signal_dates)
    signal_indices = [calendar.index(day) for day in signal_dates]
    needed_indices = sorted(
        {j for i in signal_indices for j in range(max(0, i - 1), min(len(calendar), i + 7))}
    )
    needed_dates = [calendar[i] for i in needed_indices]
    signal_price_dates = sorted({calendar[i] for i in signal_indices} | {calendar[i - 1] for i in signal_indices if i > 0})

    partition_paths = sorted(
        path for path in ENRICHED.glob("date=*/part.parquet")
        if path.parent.name[5:] in {day.isoformat() for day in needed_dates}
    )
    path_by_date = {date.fromisoformat(path.parent.name[5:]): path for path in partition_paths}
    missing_partitions = sorted(day.isoformat() for day in needed_dates if day not in path_by_date)
    input_files = [MEMBERSHIP, *partition_paths]
    manifest = {
        "round": "R307",
        "classification": "frozen_pre_execution_input_manifest_for_feasibility_only",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "preregistration_sha256": prereg_hash,
        "membership_file": file_record(MEMBERSHIP),
        "membership_rows_after_exact_key_dedup": membership.height,
        "membership_rows_with_update_after_query": late_membership.height,
        "membership_dates": len(calendar),
        "membership_date_start": calendar[0].isoformat(),
        "membership_date_end": calendar[-1].isoformat(),
        "sample_dates_sha256": hashlib.sha256(
            "\n".join(day.isoformat() for day in signal_dates).encode("ascii")
        ).hexdigest(),
        "sample_dates_count": len(signal_dates),
        "input_partitions": [file_record(path) for path in partition_paths],
        "input_file_count": len(input_files),
        "missing_required_partition_dates": missing_partitions,
        "price_columns_read": ["open", "close"],
        "future_dates_read": "Only symbol/date keys are read after each signal date. No future OHLC/price columns or forward returns are selected or computed.",
    }
    manifest_raw = (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    write_once(MANIFEST, manifest_raw)
    manifest_hash = hashlib.sha256(manifest_raw).hexdigest()
    write_once(
        MANIFEST.with_name(MANIFEST.name + ".sha256"),
        f"{manifest_hash}  {MANIFEST.name}\n".encode("ascii"),
    )

    membership_sample = membership.filter(pl.col("query_date").is_in(signal_dates))
    price_paths = [path_by_date[day] for day in signal_price_dates if day in path_by_date]
    prices = (
        pl.scan_parquet([str(path) for path in price_paths])
        .select(["symbol", "date", "open", "close"])
        .collect()
    ) if price_paths else pl.DataFrame(schema={"symbol": pl.String, "date": pl.Date, "open": pl.Float64, "close": pl.Float64})
    price_map = {(row["date"], row["symbol"]): row for row in prices.iter_rows(named=True)}

    # Future partitions are scanned for keys only; their price values are never loaded.
    key_paths = [path_by_date[day] for day in needed_dates if day in path_by_date]
    bar_keys = (
        pl.scan_parquet([str(path) for path in key_paths])
        .select(["symbol", "date"])
        .unique()
        .collect()
    ) if key_paths else pl.DataFrame(schema={"symbol": pl.String, "date": pl.Date})
    key_set = set(zip(bar_keys["date"].to_list(), bar_keys["symbol"].to_list(), strict=True))
    member_map: dict[date, list[str]] = {}
    for (day,), part in membership_sample.group_by("query_date", maintain_order=True):
        member_map[day] = part["symbol"].to_list()

    per_split: dict[str, dict] = {
        name: {
            "sampled_dates": 0,
            "member_date_keys": 0,
            "valid_current_and_previous_member_bars": 0,
            "eligible_gap_events": 0,
            "events_with_complete_holding_keys": 0,
            "missing_current_or_previous_member_keys": 0,
            "missing_holding_keys_by_event": 0,
            "event_counts_by_sampled_date": [],
        }
        for name in prereg["design"]["splits"]
    }
    for day in signal_dates:
        split = split_name(day, prereg)
        if split is None:
            continue
        stats = per_split[split]
        stats["sampled_dates"] += 1
        symbols = member_map.get(day, [])
        stats["member_date_keys"] += len(symbols)
        day_index = calendar_index[day]
        previous_day = calendar[day_index - 1] if day_index else None
        complete_events_today = 0
        for symbol in symbols:
            current = price_map.get((day, symbol))
            previous = price_map.get((previous_day, symbol)) if previous_day else None
            valid_pair = (
                current is not None
                and previous is not None
                and current["open"] is not None
                and current["close"] is not None
                and previous["close"] is not None
                and current["open"] > 0
                and current["close"] > 0
                and previous["close"] > 0
            )
            if not valid_pair:
                stats["missing_current_or_previous_member_keys"] += 1
                continue
            stats["valid_current_and_previous_member_bars"] += 1
            gap = current["open"] / previous["close"] - 1.0
            if gap < 0.005:
                continue
            stats["eligible_gap_events"] += 1
            i = day_index
            holding_dates = calendar[i + 1 : i + 7]
            complete = len(holding_dates) == 6 and all((future, symbol) in key_set for future in holding_dates)
            if complete:
                stats["events_with_complete_holding_keys"] += 1
                complete_events_today += 1
            else:
                stats["missing_holding_keys_by_event"] += 1
        stats["event_counts_by_sampled_date"].append(complete_events_today)

    rules = prereg["feasibility_gates"]
    gates: dict[str, dict] = {}
    total_pass = True
    for split, stats in per_split.items():
        coverage = (
            stats["valid_current_and_previous_member_bars"] / stats["member_date_keys"]
            if stats["member_date_keys"] else 0.0
        )
        complete_rate = (
            stats["events_with_complete_holding_keys"] / stats["eligible_gap_events"]
            if stats["eligible_gap_events"] else 0.0
        )
        counts = sorted(stats["event_counts_by_sampled_date"])
        median_daily = (
            (counts[(len(counts) - 1) // 2] + counts[len(counts) // 2]) / 2
            if counts else 0.0
        )
        stats["member_bar_coverage"] = round(coverage, 8)
        stats["holding_key_coverage"] = round(complete_rate, 8)
        stats["median_gap_events_per_sampled_date"] = median_daily
        stats["gap_events_with_complete_holding_keys"] = stats["events_with_complete_holding_keys"]
        stats.pop("event_counts_by_sampled_date")
        checks = {
            "F1_exact_membership_and_price_coverage": coverage >= 0.98,
            "F2_event_sample_dates": stats["sampled_dates"] >= 60,
            "F2_event_sample_observations": stats["events_with_complete_holding_keys"] >= 3000,
            "F2_median_events_per_sampled_date": median_daily >= 20,
            "F3_forward_bar_coverage": complete_rate >= 0.98,
        }
        gates[split] = {"checks": checks, "all_pass": all(checks.values())}
        total_pass = total_pass and gates[split]["all_pass"]

    post_manifest = [file_record(path) for path in input_files]
    manifest_by_path = {entry["path"]: entry for entry in manifest["input_partitions"]}
    manifest_by_path[manifest["membership_file"]["path"]] = manifest["membership_file"]
    input_stable = all(manifest_by_path.get(item["path"]) == item for item in post_manifest)
    result = {
        "round": "R307",
        "classification": "preregistered_feasibility_only_no_forward_return_values_read_or_computed",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "preregistration_sha256": prereg_hash,
        "input_manifest_sha256": manifest_hash,
        "input_file_count": len(input_files),
        "input_hashes_stable_after_execution": input_stable,
        "missing_required_partition_dates": missing_partitions,
        "membership": {
            "rows_after_exact_key_dedup": membership.height,
            "rows_with_update_after_query": late_membership.height,
            "dates": len(calendar),
            "date_start": calendar[0].isoformat(),
            "date_end": calendar[-1].isoformat(),
            "sample_anchor": calendar[first_i].isoformat(),
            "sampling": "every fifth R78 session from frozen first-session anchor",
        },
        "key_rows_read": bar_keys.height,
        "price_rows_read_only_on_signal_and_previous_dates": prices.height,
        "future_price_columns_read": [],
        "forward_returns_computed": False,
        "backtest_run": False,
        "alpha_scan_run": False,
        "per_split": per_split,
        "gates": gates,
        "all_feasibility_gates_pass": total_pass,
        "decision": "proceed_to_frozen_system_backtest_design" if total_pass else "NO_GO_stop_before_forward_returns",
    }
    out_raw = (json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    write_once(OUT, out_raw)
    write_once(
        OUT.with_name(OUT.name + ".sha256"),
        f"{hashlib.sha256(out_raw).hexdigest()}  {OUT.name}\n".encode("ascii"),
    )
    print(json.dumps({
        "out": OUT.as_posix(),
        "sampled_dates": {k: v["sampled_dates"] for k, v in per_split.items()},
        "split_counts": {k: {key: v[key] for key in ("member_date_keys", "valid_current_and_previous_member_bars", "eligible_gap_events", "gap_events_with_complete_holding_keys", "member_bar_coverage", "holding_key_coverage", "median_gap_events_per_sampled_date")} for k, v in per_split.items()},
        "gates": gates,
        "all_feasibility_gates_pass": total_pass,
        "forward_returns_computed": False,
        "input_hashes_stable_after_execution": input_stable,
    }, ensure_ascii=False, indent=2))
    if not input_stable:
        raise RuntimeError("R307 input files changed during feasibility audit")


if __name__ == "__main__":
    main()
