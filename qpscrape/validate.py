"""Completeness and quality report over the exported dataset."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

EXPECTED_YEARS = ("2023", "2024", "2025", "2026")


def report(json_path: str | Path = "baku_quizplease_history.json",
           duplicates_path: str | Path = "duplicates_removed.json") -> str:
    path = Path(json_path)
    if not path.exists():
        return f"No dataset at {path} -- run the scraper first."
    rows: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))

    dupes: list[dict[str, Any]] = []
    dpath = Path(duplicates_path)
    if dpath.exists():
        dupes = json.loads(dpath.read_text(encoding="utf-8"))

    dates = sorted(r["date"] for r in rows if r.get("date"))
    years = Counter(d[:4] for d in dates)
    categories = Counter((r.get("category") or r.get("theme") or "(unknown)") for r in rows)

    missing = {
        field: sum(1 for r in rows if not r.get(field))
        for field in ("date", "venue", "venue_address", "price", "start_time",
                      "title", "game_number", "image_url", "results_url")
    }

    lines = [
        "=" * 60,
        "VALIDATION REPORT -- Baku QuizPlease historical archive",
        "=" * 60,
        f"Total historical games: {len(rows)}",
        f"Earliest: {dates[0] if dates else 'n/a'}",
        f"Latest:   {dates[-1] if dates else 'n/a'}",
        f"Records with no parseable date: {sum(1 for r in rows if not r.get('date'))}",
        "",
        "By year:",
    ]
    for year in sorted(years):
        lines.append(f"  {year}: {years[year]}")
    for year in EXPECTED_YEARS:
        if year not in years:
            lines.append(f"  !! {year}: 0 -- YEAR MISSING, investigate before "
                         f"concluding no games occurred")

    lines += ["", "By category/theme:"]
    for name, count in categories.most_common(30):
        lines.append(f"  {name}: {count}")

    lines += ["", "Missing-field counts:"]
    for field, count in missing.items():
        pct = (100.0 * count / len(rows)) if rows else 0.0
        lines.append(f"  {field}: {count} ({pct:.1f}%)")

    lines += ["", f"Duplicates removed: {len(dupes)}"]
    reasons = Counter(d.get("reason", "?") for d in dupes)
    for reason, count in reasons.most_common():
        lines.append(f"  {reason}: {count}")

    sources = Counter(r.get("source", "unknown").split("|")[0] for r in rows)
    lines += ["", "By source:"]
    for name, count in sources.most_common():
        lines.append(f"  {name}: {count}")

    gaps = _month_gaps(dates)
    if gaps:
        lines += ["", "Months with zero games between first and last date "
                  "(possible archive gaps):"]
        lines += [f"  {g}" for g in gaps]

    lines.append("=" * 60)
    return "\n".join(lines)


def _month_gaps(dates: list[str]) -> list[str]:
    if len(dates) < 2:
        return []
    present = {d[:7] for d in dates}
    start_y, start_m = int(dates[0][:4]), int(dates[0][5:7])
    end_y, end_m = int(dates[-1][:4]), int(dates[-1][5:7])
    gaps = []
    year, month = start_y, start_m
    while (year, month) <= (end_y, end_m):
        key = f"{year:04d}-{month:02d}"
        if key not in present:
            gaps.append(key)
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return gaps
