
from functools import lru_cache
from datetime import date
import polars as pl
from app.config import settings

META = {
    "id": 'r307_gap_retention_top', "name": 'R307 top retention quintile',
    "description": "R307 frozen gap-retention portfolio; research only.",
    "tags": ["R307", "research-only"], "asset_types": ["stock"],
    "timeframes": ["1d"], "basic_filter": {"enabled": False},
    "required_features": ["open", "close"],
    "params": [], "scoring": {}, "order_by": "score",
    "descending": True, "limit": 500, "research_only": True,
    "execution_backend": "python_history_legacy",
}
EXECUTION_BACKEND = "python_history_legacy"
LOOKBACK_DAYS = 1
BASIC_FILTER = {"enabled": False}
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = None
MAX_HOLD_DAYS = 5
SIDE = 'top'

@lru_cache(maxsize=1)
def _membership():
    path = settings.data_dir / "research" / "r78_daily_membership" / "daily_snapshots.parquet"
    membership = pl.read_parquet(path, columns=["query_date", "update_date", "symbol"])
    if membership.filter(pl.col("update_date") > pl.col("query_date")).height:
        raise ValueError("R78 update_date exceeds query_date")
    return membership.unique(subset=["query_date", "symbol"])

def filter_history(frame: pl.DataFrame, params: dict) -> pl.DataFrame:
    del params
    membership = _membership()
    calendar = sorted(membership["query_date"].unique().to_list())
    anchor = next((i for i, day in enumerate(calendar) if day >= date.fromisoformat("2021-01-04")), None)
    if anchor is None:
        return pl.DataFrame(schema={"symbol": pl.String, "date": pl.Date})
    start, end = date.fromisoformat("2021-01-04"), date.fromisoformat("2026-09-30")
    signal_dates = [day for i, day in enumerate(calendar)
                    if i >= anchor and (i-anchor) % 5 == 0 and start <= day <= end]
    index = {day: i for i, day in enumerate(calendar)}
    bars = frame.select(["symbol", "date", "open", "close"]).unique(
        subset=["symbol", "date"], keep="last"
    )
    key_bars = frame.select("symbol", "date").unique()
    events = membership.filter(pl.col("query_date").is_in(signal_dates)).select("query_date", "symbol").join(
        bars.rename({"date": "query_date", "open": "_open", "close": "_close"}),
        on=["query_date", "symbol"], how="inner"
    ).with_columns(
        pl.col("query_date").map_elements(
            lambda day: calendar[index[day]-1] if index[day] else None, return_dtype=pl.Date
        ).alias("_previous_date")
    )
    previous = bars.select(
        "symbol", pl.col("date").alias("_previous_date"), pl.col("close").alias("_previous_close")
    )
    events = events.join(previous, on=["symbol", "_previous_date"], how="inner").filter(
        (pl.col("_open") > 0) & (pl.col("_close") > 0) & (pl.col("_previous_close") > 0)
    ).with_columns(
        (pl.col("_open") / pl.col("_previous_close") - 1.0).alias("_gap"),
        ((pl.col("_close") - pl.col("_previous_close")) / (pl.col("_open") - pl.col("_previous_close")))
            .clip(-1.0, 2.0).alias("_retention")
    ).filter(pl.col("_gap") >= 0.005)
    schedule = pl.DataFrame(
        {
            "query_date": [day for day in signal_dates for _ in range(6) if index[day]+6 < len(calendar)],
            "holding_date": [
                calendar[index[day]+offset]
                for day in signal_dates if index[day]+6 < len(calendar)
                for offset in range(1, 7)
            ],
        },
        schema={"query_date": pl.Date, "holding_date": pl.Date},
    )
    bar_keys = key_bars
    complete = events.select("query_date", "symbol").unique().join(
        schedule, on="query_date", how="inner"
    ).join(
        bar_keys.rename({"date": "holding_date"}),
        on=["symbol", "holding_date"], how="inner",
    ).group_by(["query_date", "symbol"]).agg(
        pl.len().alias("_bars")
    ).filter(pl.col("_bars") == 6).select("query_date", "symbol")
    events = events.join(complete, on=["query_date", "symbol"], how="inner")
    events = events.sort(["query_date", "_retention", "symbol"]).with_columns(
        pl.col("_retention").rank(method="ordinal").over("query_date").alias("_rank"),
        pl.len().over("query_date").alias("_n")
    )
    bucket = (pl.col("_n") * 0.2).ceil().cast(pl.UInt32).clip(lower_bound=1)
    if SIDE == "top":
        selected = events.filter(pl.col("_rank") > pl.col("_n") - bucket)
    else:
        selected = events.filter(pl.col("_rank") <= bucket)
    return selected.select(pl.col("symbol"), pl.col("query_date").alias("date")).unique()
