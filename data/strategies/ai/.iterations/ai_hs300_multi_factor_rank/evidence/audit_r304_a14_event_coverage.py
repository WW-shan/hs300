from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import polars as pl
import pyarrow.parquet as pq

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "CONTRIBUTING.md").is_file())
EVIDENCE = Path(__file__).resolve().parent
DATA = ROOT / "data"
KLINE = DATA / "kline_daily"
R78_PATH = DATA / "research/r78_daily_membership/daily_snapshots.parquet"
R157_PATH = DATA / "research/r157_membership/monthly_snapshots.parquet"
A6_PATH = EVIDENCE / "r304_A6_local_reconciliation_v3.json"
ERRATUM_PATH = EVIDENCE / "r304_A6_A7_erratum.json"
LISTING_DIR = EVIDENCE / "r304_official_archive/pages"
NOTICE_14225 = EVIDENCE / "r304_A4_14225_postload_addendum/detail_api_response.txt"
OUTPUT = EVIDENCE / "r304_A14_event_coverage_corrected.json"


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def as_date(value: object) -> date:
    return date.fromisoformat(str(value)[:10])


def ticker(code: str) -> str:
    suffix = ".SH" if code.startswith(("60", "68", "90")) else ".SZ"
    return code + suffix


def snapshots(path: Path) -> tuple[dict[date, set[str]], dict[date, date]]:
    frame = pl.read_parquet(path, columns=["query_date", "update_date", "symbol"])
    result: dict[date, set[str]] = {}
    updates: dict[date, date] = {}
    for key, group in frame.group_by("query_date", maintain_order=True):
        query_date = as_date(key[0] if isinstance(key, tuple) else key)
        result[query_date] = set(group["symbol"].cast(pl.String).to_list())
        updates[query_date] = max(as_date(value) for value in group["update_date"].to_list())
    return result, updates


def main() -> None:
    a6 = json.loads(A6_PATH.read_text(encoding="utf-8"))
    erratum = json.loads(ERRATUM_PATH.read_text(encoding="utf-8"))
    corrected_dates = {
        item["notice_id"]: item["official_effective_date"]
        for item in erratum["a6_routine_effective_dates"]["corrections"]
    }
    listings: dict[str, dict] = {}
    for path in LISTING_DIR.glob("page-*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for item in payload.get("response_json", {}).get("data", []):
            listings[str(item.get("id"))] = item

    events: list[dict] = []
    additions: dict[str, list[str]] = {}
    for item in a6["routine_2018_2021"] + a6["routine_attachment_audit"]:
        notice_id = str(item["notice_id"])
        listing = listings.get(notice_id)
        if listing is None:
            raise RuntimeError(f"Official listing missing for notice {notice_id}")
        published = listing["publishDate"]
        effective = corrected_dates.get(
            notice_id, item.get("stated_effective_date") or item["expected_effective_date"]
        )
        if len(item["added_codes"]) != item["declared_replacement_count"]:
            raise RuntimeError(f"Transcribed addition count mismatch: {notice_id}")
        additions[notice_id] = [str(code) for code in item["added_codes"]]
        events.append({
            "notice_id": notice_id,
            "period": item["event"],
            "published": published,
            "effective_after_close": effective,
            "addition_source": item.get("attachment", "official_notice_table_transcription"),
            "official_additions_count": len(additions[notice_id]),
        })

    response = json.loads(NOTICE_14225.read_text(encoding="utf-8"))
    article = response["data"]
    if article.get("id") != 14225 or article.get("publishDate") != "2022-05-27":
        raise RuntimeError("Notice 14225 identity/publication date mismatch")
    if "6月10日收市后正式生效" not in article.get("content", ""):
        raise RuntimeError("Notice 14225 effective-date text is absent")

    r78, r78_update = snapshots(R78_PATH)
    r78_dates = sorted(r78)
    before = max(d for d in r78_dates if d <= date(2022, 6, 10))
    after = min(d for d in r78_dates if d > date(2022, 6, 10))
    recovered = sorted(r78[after] - r78[before])
    additions["14225"] = [value.split(".", 1)[0] for value in recovered]
    events.append({
        "notice_id": "14225",
        "period": "2022-06",
        "published": "2022-05-27",
        "effective_after_close": "2022-06-10",
        "addition_source": "local R78 daily membership transition; not an official code list",
        "official_additions_count": 28,
        "recovered_additions_count": len(recovered),
        "recovered_addition_codes": recovered,
        "membership_before_query_date": str(before),
        "membership_after_query_date": str(after),
        "membership_before_update_date": str(r78_update[before]),
        "membership_after_update_date": str(r78_update[after]),
        "official_code_list_archived": False,
    })
    events.sort(key=lambda event: (event["published"], event["notice_id"]))
    if len(events) != 17 or len({event["notice_id"] for event in events}) != 17:
        raise RuntimeError("Expected exactly 17 unique semiannual routine events")

    partitions = sorted(KLINE.glob("date=*/part.parquet"))
    trade_dates = sorted(as_date(path.parent.name.removeprefix("date=")) for path in partitions)
    partition_by_date = {day: KLINE / f"date={day.isoformat()}" / "part.parquet" for day in trade_dates}
    schema = pq.ParquetFile(next(iter(partition_by_date.values()))).schema_arrow.names
    if not {"symbol", "date"}.issubset(schema):
        raise RuntimeError("Daily K-line schema lacks code/date fields")

    date_symbols: dict[date, set[str]] = {}
    date_duplicate_count: dict[date, int] = {}
    for event in events:
        pub = as_date(event["published"])
        eff = as_date(event["effective_after_close"])
        window = [day for day in trade_dates if pub < day < eff]
        codes = additions[event["notice_id"]]
        tickers = [ticker(code) for code in codes]
        event["window"] = {
            "first_session_strictly_after_publication": str(window[0]) if window else None,
            "last_session_strictly_before_effective_date": str(window[-1]) if window else None,
            "session_count": len(window),
        }
        missing: list[dict[str, str]] = []
        duplicate_dates: list[dict[str, object]] = []
        observed = 0
        for session in window:
            if session not in date_symbols:
                frame = pl.read_parquet(partition_by_date[session], columns=["symbol", "date"])
                values = frame["symbol"].cast(pl.String).to_list()
                date_symbols[session] = set(values)
                date_duplicate_count[session] = len(values) - len(date_symbols[session])
            present = date_symbols[session]
            for code, symbol in zip(codes, tickers, strict=True):
                if symbol in present:
                    observed += 1
                else:
                    missing.append({"date": str(session), "code": code})
            if date_duplicate_count[session]:
                duplicate_dates.append({"date": str(session), "duplicate_partition_rows": date_duplicate_count[session]})
        expected = len(tickers) * len(window)
        event["bar_code_date_coverage"] = {
            "expected_rows": expected,
            "observed_rows": observed,
            "missing_rows": expected - observed,
            "missing_code_dates": missing,
            "duplicate_dates": duplicate_dates,
            "coverage_ratio": round(observed / expected, 8) if expected else None,
        }

        effective_before = [d for d in r78_dates if d <= eff]
        effective_after = [d for d in r78_dates if d > eff]
        if effective_before and effective_after:
            m_before, m_after = effective_before[-1], effective_after[0]
            announced = {ticker(code) for code in codes}
            transition = r78[m_after] - r78[m_before]
            exact_after = next(
                (d for d in effective_after if d <= eff + timedelta(days=180) and r78[d] - r78[m_before] == announced),
                None,
            )
            if exact_after is not None:
                m_after = exact_after
                transition = r78[m_after] - r78[m_before]
            event["historical_membership_transition"] = {
                "before_query_date": str(m_before),
                "after_query_date": str(m_after),
                "before_update_not_after_query": r78_update[m_before] <= m_before,
                "after_update_not_after_query": r78_update[m_after] <= m_after,
                "observed_additions": len(transition),
                "announced_additions": len(announced),
                "exact_code_set_match": transition == announced,
                "first_exact_transition_within_180_days": exact_after is not None,
                "locally_reconstructed_list": event["notice_id"] == "14225",
            }
        else:
            event["historical_membership_transition"] = {"available": False}

    r157, r157_update = snapshots(R157_PATH)
    controls: dict[str, dict] = {}
    for event in events:
        publish = as_date(event["published"])
        available = [d for d in r78 if d < publish]
        source, source_updates = "R78 daily", r78_update
        if not available:
            available = [d for d in r157 if d < publish]
            source, source_updates = "R157 monthly", r157_update
            member_sets = r157
        else:
            member_sets = r78
        selected = available[-1] if available else None
        controls[event["notice_id"]] = {
            "source": source,
            "query_date": str(selected) if selected else None,
            "update_date": str(source_updates[selected]) if selected else None,
            "member_count": len(member_sets[selected]) if selected else 0,
            "update_date_not_after_query_date": bool(selected and source_updates[selected] <= selected),
            "strictly_preannouncement_query_date": bool(selected and selected < publish),
        }

    membership_transition_events = [event for event in events if event["notice_id"] != "14225"]
    used_partitions = sorted(date_symbols)
    input_paths = [A6_PATH, ERRATUM_PATH, NOTICE_14225, R78_PATH, R157_PATH]
    result = {
        "round": "R304-A14",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "classification": "code_date_and_row_presence_feasibility_only_no_price_or_return_fields_read",
        "preregistration_sha256": sha(EVIDENCE / "r304_A14_preregistration.json"),
        "input_sha256": {str(path.relative_to(ROOT)): sha(path) for path in input_paths},
        "official_listing_page_sha256": {str(path.relative_to(ROOT)): sha(path) for path in sorted(LISTING_DIR.glob("page-*.json"))},
        "daily_partition_sha256": {str(partition_by_date[day].relative_to(ROOT)): sha(partition_by_date[day]) for day in used_partitions},
        "columns_read_from_daily_bars": ["symbol", "date"],
        "event_count": len(events),
        "official_addition_lists_complete": sum(event["notice_id"] != "14225" for event in events),
        "local_14225_additions_recovered": len(recovered),
        "routine_events": events,
        "preannouncement_membership_snapshots": controls,
        "membership_transition_exact_matches_excluding_14225": sum(
            event.get("historical_membership_transition", {}).get("exact_code_set_match") is True
            for event in membership_transition_events
        ),
        "membership_transition_exact_matches_including_14225_local_reconstruction": sum(
            event.get("historical_membership_transition", {}).get("exact_code_set_match") is True
            for event in events
        ),
        "membership_transition_comparisons_excluding_14225": sum(
            bool(event.get("historical_membership_transition", {}).get("before_query_date"))
            for event in membership_transition_events
        ),
        "expected_bar_code_date_rows": sum(event["bar_code_date_coverage"]["expected_rows"] for event in events),
        "missing_bar_code_date_rows": sum(event["bar_code_date_coverage"]["missing_rows"] for event in events),
        "return_study_gate": "CLOSED: notice 14225 lacks an archived official addition code list; data availability does not certify control provenance or alpha.",
        "no_return_or_backtest": True,
    }
    raw = (json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    with OUTPUT.open("xb") as stream:
        stream.write(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii")
    print(json.dumps({
        "event_count": result["event_count"],
        "official_addition_lists_complete": result["official_addition_lists_complete"],
        "local_14225_additions_recovered": len(recovered),
        "membership_transition_matches": f"{result['membership_transition_exact_matches_excluding_14225']}/{result['membership_transition_comparisons_excluding_14225']}",
        "expected_bar_rows": result["expected_bar_code_date_rows"],
        "missing_bar_rows": result["missing_bar_code_date_rows"],
        "event_windows": [{"period": e["period"], "sessions": e["window"]["session_count"], "missing_rows": e["bar_code_date_coverage"]["missing_rows"]} for e in events],
        "output_sha256": hashlib.sha256(raw).hexdigest(),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
