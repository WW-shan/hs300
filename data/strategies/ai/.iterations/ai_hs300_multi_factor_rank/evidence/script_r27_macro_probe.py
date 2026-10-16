# 输入说明: 需网络 (AkShare 探测) + 本地日K。运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r27_macro_probe.py
# 作用: R27 预注册 — A 数据可得性探测 / B 宏观分层 / C 日历效应 / D 外部数据。
# 结果 JSON: 同目录 r27_macro_calendar.json; 探到的宏观序列缓存于 data/research/macro/ (不提交)。
# 说明: 单端点失败/超时不阻塞其余; 每步 flush 输出便于定位。
import json
import statistics as st
import time
from datetime import date
from pathlib import Path

import polars as pl

EV = Path(__file__).resolve().parent
REPO = EV.parents[5]
DATA = REPO / "data"
CACHE = DATA / "research" / "macro"
CACHE.mkdir(parents=True, exist_ok=True)
END = date(2026, 10, 14)


def to_ym(value) -> str | None:
    """'2008年01月份' / '2008-01' / date -> 'YYYY-MM'。"""
    text = str(value)
    if "年" in text and "月" in text:
        year, _, rest = text.partition("年")
        month = rest.partition("月")[0].zfill(2)
        return f"{year}-{month}"
    digits = "".join(ch for ch in text[:10] if ch.isdigit())
    if len(digits) >= 6:
        return f"{digits[:4]}-{digits[4:6]}"
    return None


def num(value) -> float | None:
    """带千分位/百分号的数值解析。"""
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").replace("%", "").strip())
    except ValueError:
        return None


def probe(name: str, fn) -> dict:
    t0 = time.time()
    try:
        df = fn()
        if df is None or len(df) == 0:
            rec = {"status": "empty"}
        else:
            cols = list(df.columns)
            rec = {
                "status": "ok",
                "rows": len(df),
                "columns": cols[:12],
                "seconds": round(time.time() - t0, 1),
            }
            df.to_parquet(CACHE / f"{name}.parquet")
    except Exception as exc:  # noqa: BLE001
        rec = {"status": f"error: {type(exc).__name__}: {str(exc)[:160]}", "seconds": round(time.time() - t0, 1)}
    print(f"[probe] {name}: {rec.get('status')} {rec.get('rows', '')}", flush=True)
    return rec


def main() -> int:
    import akshare as ak

    probes = {}
    probes["shibor"] = probe("shibor", lambda: ak.macro_china_shibor_all())
    probes["bond_10y"] = probe("bond_10y", lambda: ak.bond_zh_us_rate(start_date="20180101"))
    probes["pmi"] = probe("pmi", lambda: ak.macro_china_pmi_yearly())
    probes["cpi"] = probe("cpi", lambda: ak.macro_china_cpi_yearly())
    probes["ppi"] = probe("ppi", lambda: ak.macro_china_ppi_yearly())
    probes["money_supply"] = probe("money_supply", lambda: ak.macro_china_money_supply())
    probes["shrzgm"] = probe("shrzgm", lambda: ak.macro_china_shrzgm())
    probes["margin_sse"] = probe("margin_sse", lambda: ak.stock_margin_sse(start_date="20180101", end_date="20261014"))
    probes["northbound"] = probe("northbound", lambda: ak.stock_hsgt_hist_em(symbol="北向资金"))
    probes["lhb"] = probe("lhb", lambda: ak.stock_lhb_detail_em(start_date="20261008", end_date="20261014"))

    result = {"probes": probes}

    # ---------- C. 日历效应 (本地) ----------
    idx = (
        pl.scan_parquet(DATA / "kline_index_daily" / "date=*" / "part.parquet", extra_columns="ignore")
        .filter(pl.col("symbol") == "000300.SH")
        .select(["date", "close"])
        .collect()
        .sort("date")
    )
    dates = idx["date"].to_list()
    closes = idx["close"].to_list()
    gaps = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]

    def fwd(i: int, h: int) -> float | None:
        return closes[i + h] / closes[i] - 1.0 if i + h < len(closes) else None

    windows = {
        "pre_holiday_3d": [i for i in range(len(dates) - 1) if gaps[i] >= 4],
        "post_holiday_3d": [i + 1 for i in range(len(dates) - 1) if gaps[i] >= 4],
        "month_end_3d": [i for i in range(len(dates)) if i + 1 < len(dates) and dates[i].month != dates[i + 1].month],
        "june_dec": [i for i, d in enumerate(dates) if d.month in (6, 12)],
        "earnings_months": [i for i, d in enumerate(dates) if d.month in (4, 8, 10)],
    }
    calendar = {}
    for name, idxs in windows.items():
        rec = {}
        for horizon in (3, 5):
            vals = [v for i in idxs if (v := fwd(i, horizon)) is not None]
            if vals:
                all_vals = [v for i in range(len(dates)) if (v := fwd(i, horizon)) is not None]
                sd = st.pstdev(vals)
                rec[f"fwd{horizon}"] = {
                    "n": len(vals),
                    "mean": round(st.mean(vals), 5),
                    "median": round(st.median(vals), 5),
                    "baseline_mean": round(st.mean(all_vals), 5),
                    "t": round((st.mean(vals) - st.mean(all_vals)) / (sd / len(vals) ** 0.5), 2) if sd > 0 else None,
                }
        calendar[name] = rec
    # BH-FDR 10% (预注册 R27-3): 单侧 p 由正态近似 t 计算
    from statistics import NormalDist

    tests = []
    for name, rec in calendar.items():
        for horizon, values in rec.items():
            t = values.get("t")
            if t is not None:
                p = NormalDist().cdf(t)
                tests.append({"name": f"{name}_{horizon}", "t": t, "p": p})
    tests.sort(key=lambda x: x["p"])
    m = len(tests)
    for rank, item in enumerate(tests, start=1):
        item["bh_threshold"] = 0.10 * rank / m
        item["survives"] = bool(item["p"] <= item["bh_threshold"] and item["t"] > 0)
    calendar["bh_fdr"] = {"m": m, "tests": tests}
    result["calendar"] = calendar

    # ---------- B. 宏观分层 (仅 ≥5 年且解析成功的数据集) ----------
    def load(name):
        path = CACHE / f"{name}.parquet"
        return pl.read_parquet(path) if path.exists() else None

    def month_bucket(values: dict[str, float], label: str) -> dict:
        # values: 'YYYY-MM' -> bucket; 用次月等权收益比较
        monthly = {}
        for i in range(len(dates) - 1):
            if dates[i].month != dates[i + 1].month:
                monthly[str(dates[i])[:7]] = closes[i + 1] / closes[i] - 1.0
        buckets: dict[str, list[float]] = {}
        for ym, ret in monthly.items():
            b = values.get(ym)
            if b is not None:
                buckets.setdefault(b, []).append(ret)
        return {
            b: {"n": len(v), "mean": round(st.mean(v), 4), "median": round(st.median(v), 4)}
            for b, v in buckets.items()
        }

    macro_buckets = {}
    money = load("money_supply")
    if money is not None:
        cols = money.columns
        m1_col = next((c for c in cols if "M1" in str(c) and "同比" in str(c)), None)
        m2_col = next((c for c in cols if "M2" in str(c) and "同比" in str(c)), None)
        date_col = next((c for c in cols if "月份" in str(c) or "日期" in str(c)), None)
        if m1_col and m2_col and date_col:
            rows = []
            for row in money.iter_rows(named=True):
                ym = to_ym(row[date_col])
                m1 = num(row[m1_col])
                m2 = num(row[m2_col])
                if ym and m1 is not None and m2 is not None:
                    rows.append((ym, m1 - m2))
            rows.sort()
            scissors = {ym: (v, v - prev) for (ym, v), (_, prev) in zip(rows[1:], rows[:-1], strict=False)} if len(rows) > 1 else {}
            values = {ym: ("rising" if delta > 0 else "falling") for ym, (_, delta) in scissors.items()}
            macro_buckets["m1_m2_scissors"] = month_bucket(values, "m1_m2_scissors")

    bond = load("bond_10y")
    if bond is not None:
        cols = bond.columns
        dcol = next((c for c in cols if "日期" in str(c)), None)
        ycol = next((c for c in cols if "中国" in str(c) and "10" in str(c)), None)
        if dcol and ycol:
            series = []
            for row in bond.iter_rows(named=True):
                try:
                    series.append((str(row[dcol])[:10], float(row[ycol])))
                except (TypeError, ValueError):
                    continue
            series.sort()
            by_month: dict[str, float] = {}
            for d, v in series:
                by_month[d[:7]] = v
            months = sorted(by_month)
            values = {}
            for i in range(3, len(months)):
                values[months[i]] = "up" if by_month[months[i]] - by_month[months[i - 3]] > 0 else "down"
            macro_buckets["bond_10y_3m_change"] = month_bucket(values, "bond_10y")

    pmi = load("pmi")
    if pmi is not None:
        cols = pmi.columns
        dcol = next((c for c in cols if "日期" in str(c) or "月份" in str(c)), None)
        vcol = next((c for c in cols if str(c) in ("今值", "值") or "制造业" in str(c)), None)
        kind_col = next((c for c in cols if "商品" in str(c)), None)
        if dcol and vcol:
            values = {}
            for row in pmi.iter_rows(named=True):
                if kind_col and "制造" not in str(row.get(kind_col, "")):
                    continue
                ym = to_ym(row[dcol])
                v = num(row[vcol])
                if ym and v is not None:
                    values[ym] = "above50" if v >= 50 else "below50"
            macro_buckets["pmi_50"] = month_bucket(values, "pmi")

    shrzgm = load("shrzgm")
    if shrzgm is not None:
        cols = shrzgm.columns
        dcol = next((c for c in cols if "月份" in str(c) or "日期" in str(c)), None)
        vcol = next((c for c in cols if "社会融资" in str(c) and "增量" in str(c)), None) or next((c for c in cols if "增量" in str(c)), None)
        if dcol and vcol:
            series = {}
            for row in shrzgm.iter_rows(named=True):
                ym = to_ym(row[dcol])
                v = num(row[vcol])
                if ym and v is not None:
                    series[ym] = v
            months = sorted(series)
            if len(months) > 24:
                rolling = {months[i]: sum(series[m] for m in months[i - 11: i + 1]) for i in range(11, len(months))}
                values = {}
                rmonths = sorted(rolling)
                for i in range(12, len(rmonths)):
                    values[rmonths[i]] = "rising" if rolling[rmonths[i]] / rolling[rmonths[i - 12]] - 1 > 0 else "falling"
                macro_buckets["shrzgm_yoy"] = month_bucket(values, "shrzgm")

    result["macro_buckets"] = macro_buckets

    # ---------- D. 外部数据 ----------
    external = {}
    margin = load("margin_sse")
    if margin is not None:
        cols = margin.columns
        dcol = next((c for c in cols if "日期" in str(c)), None)
        vcol = next((c for c in cols if "融资余额" in str(c) and "融券" not in str(c)), None)
        if dcol and vcol:
            monthly = {}
            for row in margin.iter_rows(named=True):
                ym = to_ym(row[dcol])
                v = num(row[vcol])
                if ym and v is not None:
                    monthly[ym] = v
            months = sorted(monthly)
            changes = {months[i]: monthly[months[i]] / monthly[months[i - 1]] - 1.0 for i in range(1, len(months))}
            monthly_ret = {}
            for i in range(len(dates) - 1):
                if dates[i].month != dates[i + 1].month:
                    monthly_ret[str(dates[i])[:7]] = closes[i + 1] / closes[i] - 1.0
            pairs = [(changes[ym], monthly_ret.get(ym)) for ym in changes if monthly_ret.get(ym) is not None]
            if len(pairs) > 24:
                xs = [p[0] for p in pairs]
                ys = [p[1] for p in pairs]
                mx, my = st.mean(xs), st.mean(ys)
                numerator = sum((a - mx) * (b - my) for a, b in pairs)
                den = (sum((a - mx) ** 2 for a in xs) * sum((b - my) ** 2 for b in ys)) ** 0.5
                external["margin_sse_mom"] = {"n": len(pairs), "corr_with_next_month": round(numerator / den, 3) if den else None}
    north = load("northbound")
    if north is not None and "当日成交净买额" in north.columns:
        non_null = north.filter(pl.col("当日成交净买额").is_not_null())
        external["northbound_netbuy"] = {
            "rows_total": north.height,
            "rows_with_netbuy": non_null.height,
            "last_netbuy_date": str(non_null["日期"].max()) if non_null.height else None,
        }
    result["external"] = external

    out = EV / "r27_macro_calendar.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print("saved", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
