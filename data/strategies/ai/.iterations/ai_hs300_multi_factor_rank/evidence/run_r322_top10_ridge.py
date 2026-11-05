from __future__ import annotations

import bisect
import hashlib
import json
import math
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import numpy as np
import polars as pl

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
EVIDENCE = Path(__file__).resolve().parent
DATA = ROOT / "data"
R40 = DATA / "research/r40_engine_data"
MEMBERSHIP = DATA / "research/r78_daily_membership/daily_snapshots.parquet"
EXT = DATA / "ext_data/alt_hs300/timeseries"
PREREG = EVIDENCE / "r322_top10_ridge_preregistration.json"
OUTPUT = EVIDENCE / "r322_top10_ridge.json"
SIGNAL_START = date(2021, 6, 1)
SIGNAL_END = date(2026, 8, 31)
PRICE_START = date(2020, 12, 1)
PRICE_END = date(2026, 10, 14)
MIN_TRAIN_MONTHS = 12
MIN_TRAIN_ROWS = 1000
ALPHA = 10.0
TOP_N = 10
COST = 0.0052
FEATURES = [
    "holder_top10_free_float_share",
    "holder_top10_share_chg_qoq",
    "fund_count_low",
    "fund_holding_ratio_low",
    "fund_holding_ratio_chg_qoq_low",
    "lhb_event_count_20d_low",
    "analyst_rating_score_20d",
    "analyst_rating_change_20d",
    "forecast_surprise_60d",
    "industry_mom_20d",
    "industry_rps_20d",
    "industry_breadth_20d",
    "stock_rel_industry_20d",
    "mainline_strength",
    "margin_chg_1m_low",
    "dividend_yield",
    "equity_supply_ratio_180d_low",
    "intangible_ratio_low",
    "rd_intensity",
    "mom20",
    "mom60",
    "mom120",
    "vol20",
    "turnover20",
    "max_ret20",
    "close_position20",
]
EXT_FEATURES = FEATURES[:19]


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_hash(root: Path) -> dict:
    paths = sorted(root.glob("date=*/part.parquet"))
    digest = hashlib.sha256()
    size = 0
    for path in paths:
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(sha(path).encode())
        digest.update(b"\n")
        size += path.stat().st_size
    return {"files": len(paths), "bytes": size, "sha256": digest.hexdigest()}


def month_last_dates(dates: list[date], start: date, end: date) -> list[date]:
    chosen = []
    current = None
    for day in dates:
        if day < start or day > end:
            continue
        key = (day.year, day.month)
        if current is not None and key != current[0]:
            chosen.append(current[1])
        current = (key, day)
    if current is not None:
        chosen.append(current[1])
    return chosen


def rank_pct(values: list[float | None]) -> np.ndarray:
    out = np.full(len(values), 0.5, dtype=float)
    valid = [(i, float(v)) for i, v in enumerate(values) if v is not None and math.isfinite(float(v))]
    if not valid:
        return out
    valid.sort(key=lambda x: x[1])
    n = len(valid)
    for rank, (idx, _) in enumerate(valid):
        out[idx] = (rank + 1) / n
    return out


def load_daily() -> tuple[pl.DataFrame, list[date], dict[str, dict], dict[date, set[str]]]:
    daily = (
        pl.scan_parquet(str(R40 / "kline_daily_enriched/date=*/part.parquet"), extra_columns="ignore")
        .select(["symbol", "date", "open", "close", "turnover_rate", "trade_status"])
        .collect()
        .with_columns(pl.col("date").cast(pl.Date))
        .filter((pl.col("date") >= PRICE_START) & (pl.col("date") <= PRICE_END))
        .sort(["symbol", "date"])
    )
    calendar = sorted(daily["date"].unique().to_list())
    by_symbol: dict[str, dict] = defaultdict(lambda: {"dates": [], "open": [], "close": [], "turnover": [], "status": []})
    for row in daily.iter_rows(named=True):
        sym = str(row["symbol"])
        bucket = by_symbol[sym]
        bucket["dates"].append(row["date"])
        bucket["open"].append(row["open"])
        bucket["close"].append(row["close"])
        bucket["turnover"].append(row["turnover_rate"])
        bucket["status"].append(row["trade_status"])
    memberships = pl.read_parquet(MEMBERSHIP, columns=["query_date", "symbol"])
    by_date: dict[date, set[str]] = defaultdict(set)
    for row in memberships.iter_rows(named=True):
        by_date[row["query_date"]].add(str(row["symbol"]))
    return daily, calendar, by_symbol, by_date


def latest_membership(membership_dates: list[date], memberships: dict[date, set[str]], day: date) -> set[str]:
    idx = bisect.bisect_right(membership_dates, day) - 1
    if idx < 0:
        return set()
    return memberships.get(membership_dates[idx], set())


def ext_rows_for_date(ext_dates: list[date], ext_paths: list[Path], day: date) -> pl.DataFrame:
    idx = bisect.bisect_right(ext_dates, day) - 1
    if idx < 0:
        return pl.DataFrame()
    frame = pl.read_parquet(ext_paths[idx], columns=["symbol", *EXT_FEATURES])
    return frame.with_columns(pl.col("symbol").cast(pl.String))


def price_features(bucket: dict, day: date) -> tuple[list[float], date | None, float | None, date | None] | None:
    dates = bucket["dates"]
    idx = bisect.bisect_right(dates, day) - 1
    if idx < 0 or dates[idx] != day:
        return None
    closes = bucket["close"]
    opens = bucket["open"]
    turnovers = bucket["turnover"]
    statuses = bucket["status"]
    if str(statuses[idx]) != "1" or closes[idx] is None:
        return None
    def ret(n: int):
        j = idx - n
        if j < 0 or closes[j] is None or closes[j] == 0:
            return None
        return float(closes[idx] / closes[j] - 1.0)
    def mean(vals):
        valid = [float(v) for v in vals if v is not None and math.isfinite(float(v))]
        return sum(valid) / len(valid) if valid else None
    def std(vals):
        valid = [float(v) for v in vals if v is not None and math.isfinite(float(v))]
        if len(valid) < 2:
            return None
        m = sum(valid) / len(valid)
        return (sum((x - m) ** 2 for x in valid) / len(valid)) ** 0.5
    daily_returns = []
    for j in range(max(1, idx - 19), idx + 1):
        if closes[j - 1] and closes[j] is not None:
            daily_returns.append(float(closes[j] / closes[j - 1] - 1.0))
    window_turn = [turnovers[j] for j in range(max(0, idx - 19), idx + 1)]
    window_close = [closes[j] for j in range(max(0, idx - 19), idx + 1) if closes[j] is not None]
    if len(window_close) >= 2:
        lo, hi = min(window_close), max(window_close)
        close_pos = (float(closes[idx]) - lo) / (hi - lo) if hi > lo else 0.5
    else:
        close_pos = None
    entry = idx + 1
    exit_ = idx + 21
    forward = None
    target_end = None
    if exit_ < len(dates) and opens[entry] is not None and opens[exit_] is not None and opens[entry] != 0:
        forward = float(opens[exit_] / opens[entry] - 1.0)
        target_end = dates[exit_]
    values = [
        ret(20), ret(60), ret(120),
        std(daily_returns) * math.sqrt(252.0) if daily_returns else None,
        mean(window_turn), max(daily_returns) if daily_returns else None, close_pos,
    ]
    return values, target_end, forward, dates[idx]


def main() -> int:
    if OUTPUT.exists() or OUTPUT.with_name(OUTPUT.name + ".sha256").exists():
        raise RuntimeError("R321 result exists; refusing overwrite")
    if sha(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]:
        raise RuntimeError("R321 preregistration checksum mismatch")
    before_r40 = tree_hash(R40 / "kline_daily_enriched")
    before_prod = tree_hash(DATA / "kline_daily_enriched")
    daily, calendar, by_symbol, membership_by_date = load_daily()
    membership_dates = sorted(membership_by_date)
    ext_paths = sorted(EXT.glob("date=*/part.parquet"))
    ext_dates = [date.fromisoformat(path.parent.name.removeprefix("date=")) for path in ext_paths]
    signal_dates = month_last_dates(calendar, SIGNAL_START, SIGNAL_END)
    records_by_date: dict[date, list[dict]] = {}
    coverage_by_date = {}
    feature_nonnull_by_date = {}
    for signal in signal_dates:
        members = latest_membership(membership_dates, membership_by_date, signal)
        ext = ext_rows_for_date(ext_dates, ext_paths, signal)
        if ext.is_empty():
            records_by_date[signal] = []
            continue
        ext_by_symbol = {str(row["symbol"]): row for row in ext.iter_rows(named=True)}
        rows = []
        for symbol in sorted(members):
            if symbol not in by_symbol:
                continue
            pf = price_features(by_symbol[symbol], signal)
            if pf is None:
                continue
            price_values, target_end, forward, last_price_date = pf
            ext_row = ext_by_symbol.get(symbol, {})
            raw = [ext_row.get(name) for name in EXT_FEATURES] + price_values
            nonnull = sum(v is not None and math.isfinite(float(v)) for v in raw)
            if nonnull < 5:
                continue
            rows.append({
                "symbol": symbol,
                "signal_date": signal,
                "target_end_date": target_end,
                "forward_return": forward,
                "raw": raw,
                "nonnull": nonnull,
            })
        if rows:
            rank_vectors = [rank_pct([row["raw"][j] for row in rows]) for j in range(len(FEATURES))]
            for i, row in enumerate(rows):
                row["features"] = [float(vec[i]) for vec in rank_vectors]
                row["raw_nonnull"] = row["nonnull"]
            forward_values = [row["forward_return"] for row in rows]
            target_ranks = rank_pct(forward_values)
            for row, target in zip(rows, target_ranks):
                row["target_rank"] = float(target) if row["forward_return"] is not None else None
        records_by_date[signal] = rows
        coverage_by_date[str(signal)] = {
            "members": len(members),
            "rows": len(rows),
            "forward_nonnull": sum(row["forward_return"] is not None for row in rows),
            "mean_feature_nonnull": round(sum(row["raw_nonnull"] for row in rows) / len(rows), 4) if rows else 0.0,
        }
        feature_nonnull_by_date[str(signal)] = coverage_by_date[str(signal)]["mean_feature_nonnull"]

    prediction_rows = []
    first_prediction = None
    last_beta = None
    model_failures = []
    for signal in signal_dates:
        rows = records_by_date.get(signal, [])
        if not rows:
            continue
        train = []
        for earlier in signal_dates:
            if earlier >= signal:
                continue
            for row in records_by_date.get(earlier, []):
                if row["target_end_date"] is not None and row["target_end_date"] < signal and row["target_rank"] is not None:
                    train.append(row)
        if first_prediction is None and len(train) < MIN_TRAIN_ROWS:
            continue
        if len(train) < MIN_TRAIN_ROWS:
            continue
        if first_prediction is None:
            first_prediction = signal
        X = np.asarray([row["features"] for row in train], dtype=float)
        y = np.asarray([row["target_rank"] for row in train], dtype=float)
        mean = X.mean(axis=0)
        std = X.std(axis=0)
        std[std < 1e-9] = 1.0
        Xz = (X - mean) / std
        design = np.column_stack([np.ones(len(Xz)), Xz])
        penalty = np.eye(design.shape[1]) * ALPHA
        penalty[0, 0] = 0.0
        beta = np.linalg.solve(design.T @ design + penalty, design.T @ y)
        last_beta = beta[1:].tolist()
        current = [row for row in rows if row.get("features") is not None]
        if not current:
            continue
        Xc = (np.asarray([row["features"] for row in current], dtype=float) - mean) / std
        scores = beta[0] + Xc @ beta[1:]
        order = np.argsort(scores)[::-1]
        selected = [current[i] for i in order[:TOP_N]]
        selected_returns = [row["forward_return"] for row in selected if row["forward_return"] is not None]
        all_returns = [row["forward_return"] for row in rows if row["forward_return"] is not None]
        if not selected_returns or not all_returns:
            continue
        model_return = float(np.mean(selected_returns)) - COST
        benchmark_return = float(np.mean(all_returns)) - COST
        prediction_rows.append({
            "signal_date": str(signal),
            "train_rows": len(train),
            "train_months": len({row["signal_date"] for row in train}),
            "selected": len(selected),
            "selected_with_target": len(selected_returns),
            "model_return": round(model_return, 8),
            "benchmark_return": round(benchmark_return, 8),
            "model_return_2x_cost": round(model_return - COST, 8),
            "benchmark_return_2x_cost": round(benchmark_return - COST, 8),
            "selected_symbols": [row["symbol"] for row in selected],
        })

    if not prediction_rows:
        raise RuntimeError("no walk-forward predictions produced")
    def metrics(rows: list[dict], key: str) -> dict:
        rets = [float(row[key]) for row in rows]
        equity = [1.0]
        for r in rets:
            equity.append(equity[-1] * (1.0 + r))
        total = equity[-1] - 1.0
        annual = (1.0 + total) ** (12.0 / len(rets)) - 1.0
        sd = float(np.std(rets))
        sharpe = float(np.mean(rets) / sd * math.sqrt(12)) if sd > 0 else 0.0
        peak = equity[0]
        mdd = 0.0
        for v in equity:
            peak = max(peak, v)
            mdd = min(mdd, v / peak - 1.0)
        win = sum(r > 0 for r in rets) / len(rets)
        return {
            "months": len(rets), "total_return": round(total, 6), "annual": round(annual, 6),
            "sharpe": round(sharpe, 4), "mdd": round(mdd, 6), "positive_month_rate": round(win, 6),
        }
    model_metrics = metrics(prediction_rows, "model_return")
    benchmark_metrics = metrics(prediction_rows, "benchmark_return")
    stress_metrics = metrics(prediction_rows, "model_return_2x_cost")
    benchmark_stress = metrics(prediction_rows, "benchmark_return_2x_cost")
    monthly = [{"date": row["signal_date"], "model": row["model_return"], "benchmark": row["benchmark_return"]} for row in prediction_rows]
    yearly = {}
    for year in (2022, 2023, 2024, 2025, 2026):
        subset = [row for row in prediction_rows if int(row["signal_date"][:4]) == year]
        if subset:
            def compound(rows, key):
                out = 1.0
                for row in rows:
                    out *= 1.0 + float(row[key])
                return round(out - 1.0, 6)
            yearly[str(year)] = {"model": compound(subset, "model_return"), "benchmark": compound(subset, "benchmark_return")}
    rolling = []
    for i in range(len(prediction_rows) - 11):
        def compound_window(key):
            out = 1.0
            for row in prediction_rows[i:i + 12]:
                out *= 1.0 + float(row[key])
            return out - 1.0
        rolling.append({"model": compound_window("model_return"), "benchmark": compound_window("benchmark_return")})
    rolling_pos = sum(row["model"] > 0 for row in rolling) / len(rolling) if rolling else 0.0
    gates = {
        "D1_data": len(prediction_rows) >= 45 and all(v >= 0.60 for v in feature_nonnull_by_date.values() if float(v) >= 0.0),
        "G1_return": model_metrics["annual"] >= 0.08,
        "G2_sharpe": model_metrics["sharpe"] >= 0.70,
        "G3_drawdown": model_metrics["mdd"] >= -0.30,
        "G4_year": yearly.get("2022", {}).get("model", -1) >= 0 and yearly.get("2023", {}).get("model", -1) >= 0,
        "G5_rolling": rolling_pos >= 0.70,
        "G6_increment": (
            model_metrics["annual"] >= benchmark_metrics["annual"] + 0.03
            and model_metrics["sharpe"] >= benchmark_metrics["sharpe"] + 0.15
        ),
        "G7_cost": stress_metrics["total_return"] > 0,
        "G8_integrity": all(row["train_rows"] > 0 for row in prediction_rows),
    }
    payload = {
        "round": "R322",
        "classification": "preregistered fixed top10 ridge follow-up; no feature/model hyperparameter search",
        "preregistration_sha256": sha(PREREG),
        "runner_sha256": sha(Path(__file__).resolve()),
        "features": FEATURES,
        "model": {"type": "ridge", "alpha": ALPHA, "top_n": TOP_N, "cost": COST, "min_train_months": MIN_TRAIN_MONTHS, "min_train_rows": MIN_TRAIN_ROWS},
        "coverage": coverage_by_date,
        "feature_nonnull_by_date": feature_nonnull_by_date,
        "prediction_start": str(prediction_rows[0]["signal_date"]),
        "prediction_end": str(prediction_rows[-1]["signal_date"]),
        "model_metrics": model_metrics,
        "benchmark_metrics": benchmark_metrics,
        "model_2x_cost_metrics": stress_metrics,
        "benchmark_2x_cost_metrics": benchmark_stress,
        "rolling_12m": {"windows": len(rolling), "model_positive_rate": round(rolling_pos, 6), "benchmark_positive_rate": round(sum(r["benchmark"] > 0 for r in rolling) / len(rolling), 6) if rolling else None},
        "yearly": yearly,
        "gates": gates,
        "selected": all(gates.values()),
        "last_beta": last_beta,
        "monthly": monthly,
        "prediction_rows": prediction_rows,
        "input_isolation": {"r40_before": before_r40, "r40_after": tree_hash(R40 / "kline_daily_enriched"), "production_before": before_prod, "production_after": tree_hash(DATA / "kline_daily_enriched")},
        "decision": {
            "status": "top10_meta_factor_candidate_for_system_backtest" if all(gates.values()) else "meta_factor_research_only",
            "production_write": False, "provider_call": False, "parameter_search": False,
        },
        "limits": [
            "Monthly walk-forward ridge is not a tick-level executable backtest.",
            "Feature and membership data use the local R40 snapshot and current factor versions.",
            "The model has many features relative to the short monthly test period; walk-forward does not eliminate overfit risk.",
            "The analyst EPS revision source was found not to contain historical forecast values and was not used.",
        ],
    }
    payload["input_isolation"]["stable"] = payload["input_isolation"]["r40_before"] == payload["input_isolation"]["r40_after"] and payload["input_isolation"]["production_before"] == payload["input_isolation"]["production_after"]
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n").encode()
    with OUTPUT.open("xb") as stream:
        stream.write(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii")
    print(json.dumps({
        "prediction_period": [str(prediction_rows[0]["signal_date"]), str(prediction_rows[-1]["signal_date"])],
        "model": model_metrics, "benchmark": benchmark_metrics,
        "stress": stress_metrics, "rolling_12m": payload["rolling_12m"],
        "yearly": yearly, "gates": gates, "selected": payload["selected"],
        "output": str(OUTPUT.relative_to(ROOT)),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
