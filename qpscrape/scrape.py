"""Pipeline orchestration: sources -> detail crawl -> normalise -> dedupe -> export."""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import extract, game_page, normalize, sources
from .http import Fetcher

log = logging.getLogger(__name__)

COLUMNS = [
    "game_id", "date", "start_time", "title", "game_number", "category", "theme",
    "venue", "venue_address", "price", "currency", "duration_minutes", "rounds",
    "difficulty", "status", "description", "game_url", "results_url", "image_url",
    "city", "latitude", "longitude", "source", "scraped_at",
]


def run(
    base: str = sources.BASE,
    config_path: str | Path = "discovery/config.json",
    out_dir: str | Path = ".",
    delay: float = 0.5,
    workers: int = 4,
    use_browser: bool = True,
    crawl_details: bool = True,
    limit: int | None = None,
    respect_robots: bool = True,
) -> dict[str, Any]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    fetcher = Fetcher(delay=delay, respect_robots=respect_robots)

    config = {}
    cfg_file = Path(config_path)
    if cfg_file.exists():
        config = json.loads(cfg_file.read_text(encoding="utf-8"))
        log.info("loaded discovery config from %s", cfg_file)
    else:
        log.warning("%s not found -- run `discover` first for API-based scraping",
                    cfg_file)

    # -- gather candidates from every source ------------------------------
    provenance: dict[str, set[str]] = {}
    raw_nodes: list[dict] = []

    api_nodes = sources.from_api(config, fetcher)
    for node in api_nodes:
        raw_nodes.append(node)
    log.info("source api: %d records", len(api_nodes))

    urls: dict[str, None] = {}

    def add(url_list: list[str], label: str) -> None:
        added = 0
        for u in url_list:
            gid = sources.game_id_of(u)
            if not gid:
                continue
            provenance.setdefault(gid, set()).add(label)
            if u not in urls:
                urls[u] = None
                added += 1
        log.info("source %s: %d urls (%d new)", label, len(url_list), added)

    for node in api_nodes:
        gid = extract.pick(node, "game_id")
        if gid and extract.UUID_RE.match(str(gid)):
            provenance.setdefault(str(gid), set()).add("api")
            urls.setdefault(f"{base}/game/{gid}", None)

    add(sources.from_sitemap(fetcher, base), "sitemap")
    add(sources.from_static_pages(fetcher, base), "static")
    if use_browser:
        try:
            add(sources.from_rendered_dom(f"{base}/schedule-past"), "rendered_dom")
        except Exception as exc:
            log.warning("rendered DOM source failed: %s", exc)

    all_urls = list(urls)
    if limit:
        all_urls = all_urls[:limit]
    log.info("total distinct game URLs across sources: %d", len(all_urls))

    # -- crawl detail pages -----------------------------------------------
    details: list[dict[str, Any]] = []
    if crawl_details and all_urls:
        def fetch_one(url: str) -> dict[str, Any] | None:
            html = fetcher.get_text(url)
            if not html:
                log.warning("no HTML for %s", url)
                return None
            try:
                return game_page.parse(html, url)
            except Exception as exc:
                log.warning("parse failed for %s: %s", url, exc)
                return None

        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            for i, record in enumerate(pool.map(fetch_one, all_urls), 1):
                if record:
                    details.append(record)
                if i % 25 == 0:
                    log.info("detail crawl %d/%d", i, len(all_urls))

    # -- merge API summaries with detail pages -----------------------------
    merged: dict[str, dict[str, Any]] = {}
    for node in api_nodes:
        rec = game_page.from_node(node)
        rec["_source"] = "api"
        key = _key(rec)
        if key:
            merged[key] = rec
    duplicates: list[dict[str, str]] = []
    for rec in details:
        key = _key(rec)
        if not key:
            continue
        if key in merged:
            existing = merged[key]
            for field, value in rec.items():
                if value not in (None, "", [], {}):
                    existing[field] = value
            existing["_source"] = "+".join(
                sorted({existing.get("_source", ""), rec.get("_source", "")} - {""})
            )
        else:
            merged[key] = rec

    rows = [_normalise(rec, provenance) for rec in merged.values()]
    rows, dupes = _dedupe(rows)
    duplicates.extend(dupes)
    rows.sort(key=lambda r: (r.get("date") or "", r.get("start_time") or ""))

    csv_path = out / "baku_quizplease_history.csv"
    json_path = out / "baku_quizplease_history.json"
    _export(rows, csv_path, json_path)
    if duplicates:
        (out / "duplicates_removed.json").write_text(
            json.dumps(duplicates, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    log.info("wrote %d rows to %s and %s", len(rows), csv_path, json_path)
    return {"rows": rows, "duplicates": duplicates,
            "csv": str(csv_path), "json": str(json_path)}


def _key(rec: dict[str, Any]) -> str | None:
    gid = rec.get("game_id")
    if gid and extract.UUID_RE.match(str(gid)):
        return f"id:{gid}"
    url = rec.get("game_url")
    if url:
        found = extract.GAME_URL_RE.search(str(url))
        if found:
            return f"id:{found.group(1)}"
        return f"url:{url}"
    if gid:
        return f"id:{gid}"
    date = rec.get("date")
    title = rec.get("title")
    if date and title:
        return f"dt:{date}|{title}"
    return None


def _normalise(rec: dict[str, Any], provenance: dict[str, set[str]]) -> dict[str, Any]:
    date = normalize.norm_date(rec.get("date"))
    if not date:
        date = normalize.norm_date(rec.get("start_time"))
    price, currency = normalize.norm_price(rec.get("price"))
    if not currency:
        currency = normalize.clean_text(rec.get("currency"))
    gid = rec.get("game_id")
    if gid is not None:
        gid = str(gid)
    row = {
        "game_id": gid,
        "date": date,
        "start_time": normalize.norm_time(rec.get("start_time") or rec.get("date")),
        "title": normalize.clean_text(rec.get("title")),
        "game_number": normalize.norm_game_number(rec.get("game_number")),
        "category": normalize.clean_text(rec.get("category")),
        "theme": normalize.clean_text(rec.get("theme") or rec.get("category")),
        "venue": normalize.clean_text(rec.get("venue")),
        "venue_address": normalize.clean_text(rec.get("venue_address")),
        "price": price,
        "currency": currency or ("AZN" if price is not None else None),
        "duration_minutes": normalize.norm_duration_minutes(rec.get("duration")),
        "rounds": normalize.norm_int(rec.get("rounds")),
        "difficulty": normalize.clean_text(rec.get("difficulty")),
        "status": normalize.clean_text(rec.get("status")),
        "description": normalize.clean_text(rec.get("description")),
        "game_url": normalize.norm_url(rec.get("game_url")),
        "results_url": normalize.norm_url(rec.get("results_url")),
        "image_url": normalize.norm_url(rec.get("image_url")),
        "city": normalize.clean_text(rec.get("city")) or "Baku",
        "latitude": rec.get("latitude"),
        "longitude": rec.get("longitude"),
        "source": rec.get("_source") or "unknown",
        "scraped_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if gid and gid in provenance:
        found_in = ",".join(sorted(provenance[gid]))
        row["source"] = f"{row['source']}|found_in:{found_in}"
    return row


def _dedupe(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """UUID, then canonical URL, then date+title -- logging every drop."""
    kept: list[dict[str, Any]] = []
    removed: list[dict[str, str]] = []
    by_id: dict[str, int] = {}
    by_url: dict[str, int] = {}
    by_dt: dict[str, int] = {}

    for row in rows:
        gid = row.get("game_id")
        url = row.get("game_url")
        dt_key = f"{row.get('date')}|{(row.get('title') or '').lower()}"
        index = None
        reason = ""
        if gid and gid in by_id:
            index, reason = by_id[gid], "duplicate game_id"
        elif url and url in by_url:
            index, reason = by_url[url], "duplicate game_url"
        elif row.get("date") and row.get("title") and dt_key in by_dt:
            index, reason = by_dt[dt_key], "duplicate date+title"

        if index is not None:
            target = kept[index]
            filled = 0
            for field, value in row.items():
                if target.get(field) in (None, "") and value not in (None, ""):
                    target[field] = value
                    filled += 1
            removed.append({
                "game_id": gid or "",
                "game_url": url or "",
                "date": row.get("date") or "",
                "title": row.get("title") or "",
                "reason": reason,
                "merged_fields": str(filled),
                "kept_game_url": target.get("game_url") or "",
            })
            continue

        kept.append(row)
        position = len(kept) - 1
        if gid:
            by_id[gid] = position
        if url:
            by_url[url] = position
        if row.get("date") and row.get("title"):
            by_dt[dt_key] = position
    return kept, removed


def _export(rows: list[dict[str, Any]], csv_path: Path, json_path: Path) -> None:
    import csv as csv_mod

    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv_mod.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c) for c in COLUMNS})
    json_path.write_text(
        json.dumps([{c: r.get(c) for c in COLUMNS} for r in rows],
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
