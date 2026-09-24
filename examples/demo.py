"""
demo.py — Run this to see what using econchile feels like as a user.

Usage:
    python examples/demo.py        (from the project root)

Simulates the offline-data path (no API key needed). All queries go through
converters.py and your bundled data files. Once the live client is built,
the same user code would work against the real BCCh API with zero changes.

KEEPING IT LEAN:
    - total-macro-chile.csv  →  macro-chile-lean.csv (70 cols, down from 278)
    - Dropped: DIF_*, LN_*, DIF_LN_* transformations and their lags
    - Dropped: macrochilecsv.csv (redundant older copy)
    - Root variables: 16   Lags (_t_1 to _t_4): 48   Derived: 5
"""

import sys
import os

# Make the econchile package importable when this file runs from examples/:
#   python examples/demo.py   → repo root is one level up
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from econchile.converters import safe_float, safe_date, normalize_series
from econchile.parsers import parse_response, parse_observations
from econchile.types import Frequency, Representation, SeriesMeta, Observation
from econchile.series_map import Series

import json
import csv
from datetime import datetime
from collections import defaultdict

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
JSON_PATH = os.path.join(DATA_DIR, "chile-macro.json")
CSV_PATH = os.path.join(DATA_DIR, "macro-chile-lean.csv")  # LEAN version


def load_json_backup():
    """Load the monthly JSON backup. 16 series, 1977-2026, monthly."""
    with open(JSON_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_csv_backup():
    """Load the quarterly CSV backup. 70 columns, 1996-2023, quarterly."""
    with open(CSV_PATH, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=";")
        return list(reader)


# ─── USER-LEVEL API ────────────────────────────────────────────────────
# These simulate the public API. Users would never call safe_float/safe_date
# directly — they'd write:
#
#   from econchile import BcchClient
#   client = BcchClient()                    # reads BCCH_USER / BCCH_PASSWORD
#   df = client.get("UF", "2020-01", "2024-12")
#   df = client.get("PIB", "2020-01", "2024-12", source="csv")
#   results = client.search("empleo")

def query_series(series_name: str, source: str = "json") -> list[dict]:
    """
    Simulate: client.get_series(series_name, source=...)

    Args:
        series_name: e.g. "UF", "USD", "IPC", "TPM", "PIB"
        source: "json" (monthly, 16 series) or "csv" (quarterly, 70 cols)

    Returns:
        List of {"date": "YYYY-MM-DD", "value": float | None}, sorted by date.
    """
    if source == "json":
        raw = load_json_backup()
        results = []
        for rec in raw["data"]:
            if series_name in rec:
                val = safe_float(rec[series_name])
                date = safe_date(rec["fecha"])
                if date is not None:
                    results.append({"date": date, "value": val})
        return results

    elif source == "csv":
        raw = load_csv_backup()
        months_es = {
            "ene": "01", "feb": "02", "mar": "03", "abr": "04",
            "may": "05", "jun": "06", "jul": "07", "ago": "08",
            "sep": "09", "sept": "09", "oct": "10", "nov": "11", "dic": "12",
        }
        results = []
        for row in raw:
            periodo = row.get("Periodo", "")
            parts = periodo.split(".")
            if len(parts) == 2:
                mon = months_es.get(parts[0].lower()[:4], parts[0].lower()[:3])
                date = f"{parts[1]}-{mon}-01" if mon else None
            else:
                date = None
            val = safe_float(row.get(series_name))
            if date is not None:
                results.append({"date": date, "value": val})
        return results

    else:
        raise ValueError(f"Unknown source: {source!r}. Use 'json' or 'csv'.")


def search_series(keyword: str) -> dict:
    """
    Simulate: client.search(keyword)

    Search both offline sources for series matching a keyword.
    Returns only ROOT series (no LN_/DIF_/DIF_LN_ transformations).
    """
    json_data = load_json_backup()
    csv_data = load_csv_backup()

    matches = defaultdict(int)
    keyword_lower = keyword.lower()

    # Transformations to filter out of search results
    junk_prefixes = ("LN_", "DIF_", "DIF_LN_", "f2_", "f4_", "TC_")

    # Search JSON metadata
    for series_name in json_data["metadata"]["series"]:
        if keyword_lower in series_name.lower():
            if not any(series_name.startswith(p) for p in junk_prefixes):
                count = sum(1 for rec in json_data["data"]
                            if series_name in rec and rec[series_name] is not None)
                matches[series_name] = count

    # Search CSV headers — only root variables, no lags, no junk transformations
    if csv_data:
        csv_headers = [h for h in csv_data[0].keys() if h != "Periodo"]
        for col in csv_headers:
            if keyword_lower in col.lower():
                # Skip lags, transformations, and derived columns in search results
                if any(col.startswith(p) for p in junk_prefixes):
                    continue
                if "_t_" in col:
                    continue
                if col in ("f2_ygap", "f4_ygap", "TC_IPC", "TC_PIBNM", "TC_IPC_t_4"):
                    continue
                count = sum(1 for row in csv_data if safe_float(row.get(col)) is not None)
                matches[col] = count

    return dict(matches)


def get_lean_schema() -> dict:
    """
    Simulate: client.get_schema() — show available series, source, frequency.

    Returns a clean table of what's available in each offline source.
    """
    json_data = load_json_backup()
    csv_data = load_csv_backup()

    csv_headers = set()
    if csv_data:
        csv_headers = {h for h in csv_data[0].keys() if h != "Periodo"}

    schema = {}
    for series_name in json_data["metadata"]["series"]:
        valid = sum(1 for rec in json_data["data"] if series_name in rec and rec[series_name] is not None)
        schema[series_name] = {
            "source": "BDE (BCCh JSON)",
            "frequency": "monthly",
            "records": len(json_data["data"]),
            "valid": valid,
            "coverage": f"{valid/len(json_data['data'])*100:.1f}%",
        }
        if series_name in csv_headers:
            schema[series_name]["csv_source"] = "macro-chile-lean.csv"
            schema[series_name]["csv_frequency"] = "quarterly"

    return schema


# ─── DEMO SCENARIOS ────────────────────────────────────────────────────
#
# The first 3 scenarios simulate offline data access (JSON backup files).
# Scenarios 4-8 exercise the new API response parsing, types, and series catalog.

def banner(text: str):
    width = 62
    print(f"\\n{'=' * width}")
    print(f"  {text}")
    print(f"{'=' * width}")


def demo_parser():
    """Scenario 4: Parse a real BCCh API response (F073.TCO.PRE.Z.D)."""
    banner("SCENARIO 4: Parse real BCCh API response (USD/CLP exchange rate)")

    sample_path = os.path.join(os.path.dirname(__file__), "..", "sample_response.json")
    with open(sample_path, "rb") as f:
        raw_bytes = f.read()

    # parse_response handles UTF-16 BOM automatically
    result = parse_response(raw_bytes)

    print(f"\\n  Series ID:  {result['series_id']}")
    print(f"  Source:     {result['metadata'].get('descripIng', 'N/A')[:50]}")
    print(f"  Total obs:  {len(result['observations'])}")

    # Show first OK observation
    ok_obs = next(o for o in result["observations"] if o["value"] is not None)
    print(f"  First data: {ok_obs['date']} → {ok_obs['value']:.2f}")

    # Show ND handling
    nd_count = sum(1 for o in result["observations"] if o["value"] is None)
    print(f"  No data (ND): {nd_count} points → correctly None")

    # Verify DD-MM-YYYY conversion
    first_date = result["observations"][0]["date"]
    print(f"  Date parse: '09-08-1982' → '{first_date}' (DD-MM-YYYY → YYYY-MM-DD)")


def demo_series_map():
    """Scenario 5: Explore the series catalog (Series enum)."""
    banner("SCENARIO 5: Series catalog — 28 indexed series")

    print(f"\\n  {'Name':<14} {'Code':<28} {'Freq':<10} {'Rep':<8}")
    print(f"  {'-' * 62}")
    for s in Series.list_all():
        meta = s.meta()
        print(f"  {s.name:<14} {s.value:<28} {meta.frequency.value:<10} {meta.representation.value:<8}")

    # Show representation tags in action
    banner("SCENARIO 5b: Representation tags — IPC VAR vs INDEX")
    print(f"\\n  IPC_VAR  ({Series.IPC_VAR.value})")
    print(f"    → representation: {Series.IPC_VAR.meta().representation.value}")
    print(f"    → meaning: month-over-month % change")
    print(f"\\n  IPC_INDEX ({Series.IPC_INDEX.value})")
    print(f"    → representation: {Series.IPC_INDEX.meta().representation.value}")
    print(f"    → meaning: index level (base 2023=100)")
    print(f"\\n  Same variable (IPC), different representations — the user must choose.")


def demo_types():
    """Scenario 6: Show frozen dataclasses (SeriesMeta, Observation)."""
    banner("SCENARIO 6: Types — frozen SeriesMeta & Observation")

    meta = Series.USD.meta()
    print(f"\\n  SeriesMeta for USD:")
    print(f"    series_id:     {meta.series_id}")
    print(f"    spanish_title: {meta.spanish_title[:40]}...")
    print(f"    english_title: {meta.english_title[:40]}...")
    print(f"    frequency:     {meta.frequency.value}")
    print(f"    representation: {meta.representation.value}")

    obs = Observation(date="2024-01-15", value=923.74)
    print(f"\\n  Observation:")
    print(f"    date:  {obs.date}")
    print(f"    value: {obs.value}")
    print(f"    frozen: {hasattr(obs, '__dataclass_fields__')} (try to mutate and you get FrozenInstanceError)")


def demo_query():
    """Scenario 1: User queries a single series."""
    banner("SCENARIO 1: Query UF (monthly, JSON)")

    data = query_series("UF", "json")
    recent = [r for r in data if r["date"] >= "2020-01-01"]

    print(f"\n  Records returned: {len(recent)}")
    print(f"  First:  {recent[0]['date']}  →  {recent[0]['value']:>12,.2f}")
    print(f"  Last:   {recent[-1]['date']}  →  {recent[-1]['value']:>12,.2f}")


def demo_csv_query():
    """Scenario 2: CSV PIB vs JSON PIB — same name, different meaning."""
    banner("SCENARIO 2: Query PIB — JSON vs CSV (different representations)")

    json_data = query_series("PIB", "json")
    csv_data = query_series("PIB", "csv")

    print(f"\n  JSON PIB:  {len(json_data)} monthly records — YoY growth %")
    print(f"    Sample: {json_data[0]['date']} → {json_data[0]['value']}")
    print(f"  CSV  PIB:  {len(csv_data)} quarterly records — level (CLP billions)")
    print(f"    Sample: {csv_data[0]['date']} → {csv_data[0]['value']}")
    print(f"\n  → User must choose: source='json' (growth%) or source='csv' (level)")


def demo_search():
    """Scenario 3: User searches for 'empleo' — clean root results only."""
    banner("SCENARIO 3: Search 'empleo' — root series only (no garbage)")

    results = search_series("empleo")
    print(f"\n  Found {len(results)} root series:")
    for name, count in sorted(results.items(), key=lambda x: -x[1]):
        print(f"    {name:25s}: {count:4d} values")


def demo_search_copper():
    """Scenario 4: Search 'cobre' — found only in JSON."""
    banner("SCENARIO 4: Search 'cobre' (copper price, JSON-only)")

    results = search_series("cobre")
    if results:
        for name, count in sorted(results.items(), key=lambda x: -x[1]):
            print(f"    {name:25s}: {count:4d} values")
    else:
        print("    No matches.")


def demo_null_quality():
    """Scenario 5: Show null density and gap patterns."""
    banner("SCENARIO 5: Data quality — USD null density")

    usd = query_series("USD", "json")
    valid = sum(1 for r in usd if r["value"] is not None)
    nulls = len(usd) - valid

    gap_starts = []
    prev_null = False
    for r in usd:
        if r["value"] is None:
            if not prev_null:
                gap_starts.append(r["date"])
            prev_null = True
        else:
            prev_null = False

    print(f"\n  Total: {len(usd)}  |  Valid: {valid} ({valid/len(usd)*100:.1f}%)  |  Null: {nulls} ({nulls/len(usd)*100:.1f}%)")
    print(f"  First gaps: {gap_starts[:5]}")


def demo_multi_series():
    """Scenario 6: Multi-series comparison table."""
    banner("SCENARIO 6: IPC + TPM + DESEMPLEO (monthly, JSON)")

    print(f"\n  {'Date':<12} {'IPC (MoM%)':>14} {'TPM (%)':>10} {'Desempleo (%)':>16}")
    print(f"  {'-' * 54}")

    ipc = {r["date"]: r["value"] for r in query_series("IPC", "json")}
    tpm = {r["date"]: r["value"] for r in query_series("TPM", "json")}
    des = {r["date"]: r["value"] for r in query_series("DESEMPLEO", "json")}

    common = sorted(set(ipc) & set(tpm) & set(des))
    sample = [d for d in common if d >= "2023-01-01"]

    for date in sample[:12]:
        vi = f"{ipc[date]:+.3f}" if ipc[date] is not None else "  N/A"
        vt = f"{tpm[date]:.2f}"  if tpm[date] is not None else "  N/A"
        vd = f"{des[date]:.2f}%" if des[date] is not None else "  N/A"
        print(f"  {date:<12} {vi:>14} {vt:>10} {vd:>16}")


def demo_schema():
    """Scenario 7: Show the lean schema — what series exist and where."""
    banner("SCENARIO 7: Lean schema — available series by source")

    schema = get_lean_schema()

    print(f"\n  Total series across both sources: {len(schema)}")
    print(f"\n  {'Series':<20} {'Source':<22} {'Freq':<10} {'Valid':>6}")
    print(f"  {'-' * 62}")

    for name in sorted(schema):
        info = schema[name]
        src = info.get("csv_source", info.get("source", ""))
        freq = info.get("csv_frequency", info.get("frequency", ""))
        valid = info["valid"]
        print(f"  {name:<20} {src:<22} {freq:<10} {valid:>6,}")


# ─── RUN ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"\n{'#' * 62}")
    print("#  econchile — simulated user experience")
    print(f"#  JSON: {os.path.basename(JSON_PATH)}")
    print(f"#  CSV:  {os.path.basename(CSV_PATH)}")
    print("#" * 62)

    demo_query()
    demo_csv_query()
    demo_search()
    demo_search_copper()
    demo_null_quality()
    demo_multi_series()
    demo_schema()

    # New scenarios exercising parsers, series_map, types
    demo_parser()
    demo_series_map()
    demo_types()

    print(f"\n{'#' * 62}")
    print("#  End of demo.")
    print("#" * 62 + "\n")

    print("TECHNICAL NOTE:")
    print("  In the real library, all these calls go through:")
    print("    1. BcchClient (reads API credentials from env)")
    print("    2. Cache layer (checks SQLite before hitting network)")
    print("    3. Async fetcher (concurrent requests with retry + backoff)")
    print("    4. converters.py (safe_float/safe_date clean the raw response)")
    print("    5. Offline fallback (JSON → lean CSV if API is down)")
    print(f"\n  LEAN DATA POLICY:")
    print(f"    - 278-column CSV trimmed to 70 columns")
    print(f"    - Dropped: DIF_*, LN_*, DIF_LN_* and their lags")
    print(f"    - Root variables: 16 | Lagged: 48 | Derived: 5")
    print(f"    - Redundant macrochilecsv.csv removed from active sources")
    print()
