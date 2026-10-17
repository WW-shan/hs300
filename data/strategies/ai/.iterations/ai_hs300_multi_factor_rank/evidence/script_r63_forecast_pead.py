# 输入说明: R63 — 历史 HS300 业绩预告预期差事件漂移。进程内运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r63_forecast_pead.py
# 结果 JSON: 同目录 r63_forecast_pead.json。逐季度原始缓存 data/research/r63_forecast/ (不提交)。
from __future__ import annotations

import bisect
import importlib.util
import json
import statistics as st
import time
from datetime import date
from pathlib import Path

import polars as pl

EV = Path(__file__).resolve().parent


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, EV / filename)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


r42 = _load("r42", "script_r42_historical_factor_screen.py")
r40 = _load("r40", "script_r40_survivorship.py")
r43 = _load("r43", "script_r43_lhb_event.py")
r22 = r42.r22
DATA = r42.DATA
CACHE = DATA / "research" / "r63_forecast"
START, END = r42.DISCOVERY[0], r42.VALIDATION[1]
DEoverlap_GAP = 60
COST_2X = 0.0028
MIN_MONTH_EVENTS = 10
QUARTERS = [
    date(2020, 12, 31), date(2021, 3, 31), date(2021, 6, 30), date(2021, 9, 30),
    date(2021, 12, 31), date(2022, 3, 31), date(2022, 6, 30), date(2022, 9, 30),
    date(2022, 12, 31), date(2023, 3, 31), date(2023, 6, 30), date(2023, 9, 30),
    date(2023, 12, 31), date(2024, 3, 31), date(2024, 6, 30), date(2024, 9, 30),
    date(2024, 12, 31), date(2025, 3, 31), date(2025, 6, 30), date(2025, 9, 30),
    date(2025, 12, 31), date(2026, 3, 31), date(2026, 6, 30), date(2026, 9, 30),
]


def _fetch() -> dict:
    import akshare as ak

    CACHE.mkdir(parents=True, exist_ok=True)
    failures: dict[str, str] = {}
    empty: list[str] = []
    fetched = 0
    cached = 0
    for index, quarter in enumerate(QUARTERS, start=1):
        path = CACHE / f"{quarter.isoformat()}.parquet"
        if path.exists() and path.stat().st_size > 0:
            cached += 1
            continue
        last_error = None
        for attempt in range(3):
            try:
                raw = ak.stock_yjyg_em(date=quarter.strftime("%Y%m%d"))
                frame = pl.from_pandas(raw)
                if frame.height == 0:
                    empty.append(quarter.isoformat())
                tmp = path.with_suffix(".tmp")
                frame.write_parquet(tmp)
                tmp.replace(path)
                fetched += 1
                last_error = ""
                break
            except Exception as exc:  # noqa: BLE001 - provider failures are recorded
                last_error = f"{type(exc).__name__}: {str(exc)[:180]}"
                if attempt < 2:
                    time.sleep(1.0 * (attempt + 1))
        if last_error:
            failures[quarter.isoformat()] = last_error
        if index % 4 == 0 or index == len(QUARTERS):
            print(f"forecast fetch {index}/{len(QUARTERS)} cached={cached} fetched={fetched} failed={len(failures)}", flush=True)
    manifest = {
        "quarters": len(QUARTERS), "cached_this_run": cached, "fetched_this_run": fetched,
        "empty_quarters": empty, "failure_count": len(failures),
        "failure_rate": round(len(failures) / len(QUARTERS), 6), "failures": failures,
    }
    (CACHE / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return manifest


def _events(symbols: set[str]) -> tuple[pl.DataFrame, dict]:
    frames = []
    for quarter in QUARTERS:
        path = CACHE / f"{quarter.isoformat()}.parquet"
        if not path.exists():
            continue
        frame = pl.read_parquet(path).with_columns(pl.lit(quarter).alias("report_date"))
        frames.append(frame)
    if not frames:
        raise RuntimeError("业绩预告缓存为空")
    raw = pl.concat(frames, how="diagonal_relaxed")
    required = {"股票代码", "预测指标", "业绩变动幅度", "公告日期"}
    missing = sorted(required - set(raw.columns))
    if missing:
        raise ValueError(f"业绩预告字段缺失: {missing}")
    instruments = pl.read_parquet(DATA / "instruments" / "instruments.parquet").select(["symbol", "code"])
    frame = raw.select([
        pl.col("股票代码").cast(pl.Utf8).str.extract(r"(\d{6})", 1).str.zfill(6).alias("code"),
        pl.col("预测指标").cast(pl.Utf8).str.replace_all(" ", "").alias("indicator"),
        pl.col("业绩变动幅度").cast(pl.Float64, strict=False).alias("surprise"),
        pl.col("公告日期").cast(pl.Date, strict=False).alias("announce_date"),
        pl.col("report_date").cast(pl.Date),
    ]).join(instruments, on="code", how="inner")
    principal = pl.col("indicator") == "归属于上市公司股东的净利润"
    exact = pl.col("indicator") == "净利润"
    typed = frame.filter(pl.col("symbol").is_in(symbols)).with_columns(
        pl.when(principal).then(1).when(exact).then(2).otherwise(None).alias("priority")
    ).filter(pl.col("priority").is_not_null())
    priority = typed.group_by(["symbol", "report_date", "announce_date"]).agg(pl.col("priority").min().alias("priority"))
    selected = typed.join(priority, on=["symbol", "report_date", "announce_date", "priority"], how="inner")
    events = selected.group_by(["symbol", "report_date", "announce_date"]).agg([
        pl.col("surprise").median().alias("surprise"),
        pl.len().alias("source_rows"),
    ]).filter(pl.col("announce_date").is_not_null() & pl.col("surprise").is_not_null())
    audit = {
        "raw_rows": raw.height,
        "principal_or_exact_rows": typed.height,
        "normalized_events": events.height,
        "symbols": events["symbol"].n_unique(),
        "months": events["announce_date"].dt.strftime("%Y-%m").n_unique(),
        "announce_null_rate": round(float(selected["announce_date"].is_null().mean()), 6) if selected.height else 1.0,
        "surprise_null_rate": round(float(selected["surprise"].is_null().mean()), 6) if selected.height else 1.0,
        "report_date_min": str(events["report_date"].min()) if events.height else None,
        "report_date_max": str(events["report_date"].max()) if events.height else None,
        "announce_min": str(events["announce_date"].min()) if events.height else None,
        "announce_max": str(events["announce_date"].max()) if events.height else None,
    }
    return events, audit


def _car_rows(events: pl.DataFrame, series, bench, dates: list[date]) -> pl.DataFrame:
    rows = []
    for row in events.iter_rows(named=True):
        item = series.get(row["symbol"])
        if not item:
            continue
        sym_dates, opens = item
        i = bisect.bisect_right(sym_dates, row["announce_date"])
        out = dict(row)
        for horizon in (20, 60):
            if i + horizon >= len(sym_dates) or opens[i] <= 0 or opens[i + horizon] <= 0:
                out[f"car{horizon}"] = None
                continue
            stock = opens[i + horizon] / opens[i] - 1.0
            bench_ret = 1.0
            for j in range(i, i + horizon):
                bench_ret *= 1.0 + bench.get(sym_dates[j], 0.0)
            out[f"car{horizon}"] = stock - (bench_ret - 1.0)
        rows.append(out)
    if not rows:
        return pl.DataFrame()
    return pl.DataFrame(rows).with_columns(pl.col("announce_date").dt.strftime("%Y-%m").alias("month"))


def _deoverlap(frame: pl.DataFrame, dates: list[date], min_gap: int = DEoverlap_GAP) -> tuple[pl.DataFrame, int]:
    date_index = {day: index for index, day in enumerate(dates)}
    rows = []
    last: dict[str, int] = {}
    for row in frame.sort(["symbol", "announce_date"]).iter_rows(named=True):
        symbol = row["symbol"]
        idx = date_index.get(row["announce_date"])
        if idx is None:
            continue
        if symbol in last and idx - last[symbol] < min_gap:
            continue
        last[symbol] = idx
        row["_date_index"] = idx
        rows.append(row)
    return pl.DataFrame(rows).sort(["symbol", "announce_date"]) if rows else pl.DataFrame(), frame.height


def _monthly_ic(frame: pl.DataFrame, horizon: int) -> dict:
    values = []
    for month in sorted(frame["month"].unique().to_list()):
        d = frame.filter((pl.col("month") == month) & pl.col(f"car{horizon}").is_not_null())
        if d.height < MIN_MONTH_EVENTS or d["surprise"].n_unique() < 2:
            continue
        ic = r22.spearman(d["surprise"].to_list(), d[f"car{horizon}"].to_list())
        if ic is not None:
            values.append((month, ic))
    if not values:
        return {"n": 0, "mean": None, "t": None, "yearly": {}}
    ics = [v for _, v in values]
    mean = st.mean(ics)
    sd = st.pstdev(ics)
    yearly = {}
    for year in range(2021, 2027):
        yearly_ics = [ic for month, ic in values if int(month[:4]) == year]
        if yearly_ics:
            yearly[str(year)] = round(st.mean(yearly_ics), 4)
    return {
        "n": len(ics), "mean": round(mean, 4),
        "t": round(mean / (sd / len(ics) ** 0.5), 2) if sd > 0 else None,
        "yearly": yearly,
        "values": [{"month": month, "ic": round(ic, 4)} for month, ic in values],
    }


def _q_gate(frame: pl.DataFrame, horizon: int) -> dict:
    stats = r43._event_stats(frame, f"car{horizon}")
    means = [row["mean"] for row in stats]
    top = stats[-1]
    rho = round(r22.spearman(list(range(5)), means), 3) if all(v is not None for v in means) and len(set(means)) > 1 else 0.0
    return {
        "by_quintile": stats, "monotonic_rho": rho,
        "top_median": top["median"], "top_t": top["t"],
        "pass": bool(
            rho >= 0.7 and top["median"] is not None and top["median"] > 0
            and top["t"] is not None and top["t"] > 2.0
        ),
    }


def _yearly_q5(frame: pl.DataFrame, horizon: int) -> dict:
    q5 = frame.filter(pl.col("q") == 4)
    out = {}
    for year in range(2021, 2027):
        vals = q5.filter(pl.col("announce_date").dt.year() == year)[f"car{horizon}"].drop_nulls().to_list()
        out[str(year)] = {"n": len(vals), "median": round(st.median(vals), 5) if vals else None}
    return out


def main() -> int:
    manifest = _fetch()
    memberships, _ = r40.membership_frame()
    symbols = set(memberships["symbol"].unique().to_list())
    events, event_audit = _events(symbols)
    events = events.filter((pl.col("announce_date") >= START) & (pl.col("announce_date") <= END))
    from app.backtest.strategy import _hs300_panel_row_mask

    events = events.with_columns(pl.col("announce_date").alias("date"))
    events = events.filter(_hs300_panel_row_mask(events, memberships).fill_null(False))
    combined, _ = r40.load_combined_prices(sorted(symbols))
    series, bench, dates = r43._series_and_bench(combined, memberships)
    rows = _car_rows(events, series, bench, dates)
    rows, before_deoverlap = _deoverlap(rows, dates)
    rows = rows.with_columns([
        pl.when(pl.col("announce_date") <= r42.DISCOVERY[1]).then(pl.lit("discovery")).otherwise(pl.lit("validation")).alias("period"),
        pl.col("announce_date").dt.year().alias("year"),
        ((pl.col("surprise").rank("ordinal").over("month") - 1.0) * 5.0 / pl.len().over("month"))
        .floor().clip(0, 4).cast(pl.Int64).alias("q"),
    ])
    print("manifest", manifest, "events", event_audit, "rows", rows.shape, "before_deoverlap", before_deoverlap, flush=True)

    ic20 = _monthly_ic(rows, 20)
    ic60 = _monthly_ic(rows, 60)
    disc60 = _monthly_ic(rows.filter(pl.col("period") == "discovery"), 60)
    val60 = _monthly_ic(rows.filter(pl.col("period") == "validation"), 60)
    q20 = _q_gate(rows, 20)
    q60 = _q_gate(rows, 60)
    disc_q60 = r43._event_stats(rows.filter((pl.col("period") == "discovery") & pl.col("q").is_in([0, 1, 2, 3, 4])), "car60")
    val_q60 = r43._event_stats(rows.filter(pl.col("period") == "validation"), "car60")
    val_q20 = r43._event_stats(rows.filter(pl.col("period") == "validation"), "car20")
    yearly60 = _yearly_q5(rows, 60)
    yearly_positive = sum(1 for year in ("2022", "2023", "2024", "2025", "2026") if yearly60[year]["median"] is not None and yearly60[year]["median"] > 0)
    d1 = bool(
        manifest["failure_rate"] <= 0.10 and event_audit["normalized_events"] >= 1000
        and event_audit["symbols"] >= 150 and event_audit["months"] >= 48
        and event_audit["announce_null_rate"] <= 0.10 and event_audit["surprise_null_rate"] <= 0.10
    )
    yearly_ic_same = r22.yearly_same_sign(ic60["yearly"], ic60["mean"] or 0)
    f1 = bool(
        ic60["mean"] is not None and disc60["mean"] is not None and val60["mean"] is not None
        and disc60["mean"] >= 0.03 and val60["mean"] >= 0.02 and disc60["mean"] > 0 and val60["mean"] > 0
        and yearly_ic_same >= 4
    )
    f2 = bool(q20["pass"] and q60["pass"])
    val60_top = val_q60[-1]
    val20_top = val_q20[-1]
    f3 = bool(
        disc_q60[-1]["median"] is not None and disc_q60[-1]["median"] > 0
        and val60_top["median"] is not None and val60_top["median"] > 0
        and val60_top["t"] is not None and val60_top["t"] > 1.0
        and val20_top["median"] is not None and val20_top["median"] > 0
    )
    f4 = bool(
        q20["top_median"] is not None and q20["top_median"] > COST_2X
        and q60["top_median"] is not None and q60["top_median"] > COST_2X
        and yearly_positive >= 4
    )
    gates = {"D1": d1, "F1": f1, "F2": f2, "F3": f3, "F4": f4}
    gates["all_pass"] = all(gates.values())
    result = {
        "window": {"start": str(START), "end": str(END), "discovery": [str(r42.DISCOVERY[0]), str(r42.DISCOVERY[1])], "validation": [str(r42.VALIDATION[0]), str(r42.VALIDATION[1])]},
        "rules": {"quarters": len(QUARTERS), "dedup_gap_trading_days": DEoverlap_GAP, "cost_2x": COST_2X, "min_month_events": MIN_MONTH_EVENTS},
        "data": {"fetch": manifest, **event_audit, "events_after_membership": events.height, "events_before_deoverlap": before_deoverlap, "events_after_deoverlap": rows.height},
        "ic": {"car20": ic20, "car60": ic60, "discovery_car60": disc60, "validation_car60": val60},
        "quintiles": {"car20": q20, "car60": q60},
        "split_q5": {"discovery_car60": disc_q60[-1], "validation_car60": val60_top, "validation_car20": val20_top},
        "yearly_q5_car60": yearly60,
        "gates": gates,
        "passed": ["FORECAST_PEAD"] if gates["all_pass"] else [],
        "recommendation": "R64 forecast integration" if gates["all_pass"] else "negative knowledge; forecast dimension closed",
        "caveats": ["只使用公告日", "归母净利润优先, 精确净利润兜底, 排除扣非", "同股 60 交易日去重叠", "公告后 T+1 开盘进入", "历史名单为月度快照"],
    }
    path = EV / "r63_forecast_pead.json"
    path.write_text(json.dumps(r22.compact(result), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("IC20", ic20["mean"], "IC60", ic60["mean"], "disc/val60", disc60["mean"], val60["mean"], "q20/q60", q20["pass"], q60["pass"], "gates", gates, "saved", path, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
