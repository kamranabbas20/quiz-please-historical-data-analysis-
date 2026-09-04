"""Step 1 of the pipeline: find out how the site actually loads its archive.

Drives a real browser at ``/schedule-past``, records every XHR/fetch response,
exhausts infinite scroll and "load more" buttons, then ranks the observed
endpoints by how many game-shaped records they returned.  The winning endpoint
is written to ``discovery/config.json`` so the scraper can query the backend
directly instead of driving a browser.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import extract

log = logging.getLogger(__name__)

INTERESTING = ("game", "schedule", "past", "event", "quiz", "archive",
               "list", "api", "graphql", "city", "search")
SCROLL_PAUSE_MS = 1500
MAX_SCROLLS = 400
LOAD_MORE_TEXT = re.compile(
    r"(показать|загрузить|ещё|еще|далее|более|more|load|daha|göstər|növbəti)",
    re.I,
)


def _executable_path() -> str | None:
    """Prefer a pre-installed Chromium over one Playwright would download."""
    for candidate in sorted(Path("/opt/pw-browsers").glob("chromium*/chrome-linux/chrome")):
        return str(candidate)
    return None


def discover(
    url: str = "https://baku.quizplease.com/schedule-past",
    out_dir: str | Path = "discovery",
    headless: bool = True,
    settle_ms: int = 8000,
    max_scrolls: int = MAX_SCROLLS,
) -> dict[str, Any]:
    """Load ``url`` in a browser and record everything it fetches."""
    from playwright.sync_api import sync_playwright

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    captured: list[dict[str, Any]] = []

    with sync_playwright() as p:
        launch: dict[str, Any] = {"headless": headless}
        exe = _executable_path()
        if exe:
            launch["executable_path"] = exe
        browser = p.chromium.launch(**launch)
        context = browser.new_context(
            locale="ru-RU",
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like "
                "Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()

        def on_response(response) -> None:
            try:
                req = response.request
                if req.resource_type not in ("xhr", "fetch", "document", "script"):
                    return
                low = response.url.lower()
                ctype = (response.headers or {}).get("content-type", "")
                is_json = "json" in ctype
                if not is_json and not any(k in low for k in INTERESTING):
                    return
                record: dict[str, Any] = {
                    "url": response.url,
                    "method": req.method,
                    "status": response.status,
                    "resource_type": req.resource_type,
                    "content_type": ctype,
                    "request_headers": dict(req.headers),
                    "post_data": req.post_data,
                    "query": {k: v for k, v in parse_qs(urlparse(response.url).query).items()},
                }
                if is_json:
                    try:
                        body = response.json()
                    except Exception:
                        body = None
                    if body is not None:
                        nodes = extract.find_game_nodes(body)
                        record["game_node_count"] = len(nodes)
                        record["sample_node"] = nodes[0] if nodes else None
                        record["top_level_keys"] = (
                            list(body.keys())[:40] if isinstance(body, dict) else f"list[{len(body)}]"
                        )
                        record["body"] = body
                captured.append(record)
            except Exception as exc:  # never let logging break the crawl
                log.debug("response handler error: %s", exc)

        page.on("response", on_response)
        log.info("loading %s", url)
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_load_state("networkidle", timeout=settle_ms)
        except Exception:
            pass
        page.wait_for_timeout(2000)

        _exhaust(page, max_scrolls)

        html = page.content()
        (out / "schedule_past_rendered.html").write_text(html, encoding="utf-8")
        links = extract.game_links(html, url)
        (out / "game_links_from_dom.json").write_text(
            json.dumps(links, indent=2), encoding="utf-8"
        )
        log.info("rendered DOM exposes %d distinct /game/ links", len(links))

        hydration = extract.hydration_blobs(html) + extract.inline_json_blobs(html)
        if hydration:
            (out / "hydration_blobs.json").write_text(
                json.dumps(hydration, ensure_ascii=False, indent=2)[:8_000_000],
                encoding="utf-8",
            )
        browser.close()

    report = _rank(captured, out)
    report["dom_game_links"] = links
    report["hydration_game_nodes"] = sum(
        len(extract.find_game_nodes(b)) for b in hydration
    )
    (out / "network_log.json").write_text(
        json.dumps(captured, ensure_ascii=False, indent=2)[:20_000_000],
        encoding="utf-8",
    )
    (out / "report.json").write_text(
        json.dumps({k: v for k, v in report.items() if k != "candidates_full"},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report


def _exhaust(page, max_scrolls: int) -> None:
    """Scroll and click "load more" until the game-link count stops growing."""
    previous = -1
    stagnant = 0
    for i in range(max_scrolls):
        count = page.locator("a[href*='/game/']").count()
        if count == previous:
            stagnant += 1
        else:
            stagnant = 0
        if stagnant >= 2:
            log.info("archive exhausted after %d scroll rounds (%d links)", i, count)
            break
        previous = count
        clicked = _click_load_more(page)
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(SCROLL_PAUSE_MS if not clicked else SCROLL_PAUSE_MS + 800)
    else:
        log.warning("hit max_scrolls=%d -- archive may be incomplete", max_scrolls)


def _click_load_more(page) -> bool:
    for element in page.locator("button, a, div[role='button'], span").all()[:400]:
        try:
            if not element.is_visible():
                continue
            text = (element.inner_text() or "").strip()
            if text and len(text) < 40 and LOAD_MORE_TEXT.search(text):
                element.click(timeout=2500)
                return True
        except Exception:
            continue
    return False


def _rank(captured: list[dict[str, Any]], out: Path) -> dict[str, Any]:
    """Score endpoints by how many game records they yielded."""
    by_endpoint: dict[str, dict[str, Any]] = {}
    for rec in captured:
        parsed = urlparse(rec["url"])
        key = f"{rec['method']} {parsed.scheme}://{parsed.netloc}{parsed.path}"
        slot = by_endpoint.setdefault(key, {
            "endpoint": key,
            "method": rec["method"],
            "url_template": f"{parsed.scheme}://{parsed.netloc}{parsed.path}",
            "calls": 0,
            "game_nodes": 0,
            "query_params": {},
            "sample_url": rec["url"],
            "request_headers": rec.get("request_headers", {}),
            "post_data": rec.get("post_data"),
            "top_level_keys": rec.get("top_level_keys"),
            "sample_node": rec.get("sample_node"),
        })
        slot["calls"] += 1
        slot["game_nodes"] += rec.get("game_node_count", 0) or 0
        for k, v in (rec.get("query") or {}).items():
            slot["query_params"].setdefault(k, []).extend(v)

    ranked = sorted(by_endpoint.values(), key=lambda s: (-s["game_nodes"], -s["calls"]))
    best = next((r for r in ranked if r["game_nodes"] > 0), None)
    config = {
        "api": None if not best else {
            "url": best["url_template"],
            "method": best["method"],
            "query_params": {k: v[0] for k, v in best["query_params"].items() if v},
            "pagination": _guess_pagination(best),
            "headers": {
                k: v for k, v in (best.get("request_headers") or {}).items()
                if k.lower() in ("accept", "content-type", "x-requested-with",
                                 "authorization", "x-csrf-token", "referer",
                                 "accept-language")
            },
            "post_data": best.get("post_data"),
        },
    }
    (out / "config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "candidates": [
            {k: v for k, v in r.items() if k not in ("sample_node", "request_headers")}
            for r in ranked[:25]
        ],
        "best_endpoint": best["endpoint"] if best else None,
        "suggested_config": config,
    }


def _guess_pagination(slot: dict[str, Any]) -> dict[str, Any]:
    """Infer the pagination knob from the query params the site itself used."""
    params = {k.lower() for k in slot.get("query_params", {})}
    for name in ("page", "p", "pageNumber", "pageNum"):
        if name.lower() in params:
            return {"style": "page", "param": name, "start": 1, "step": 1}
    for name in ("offset", "skip", "from", "start"):
        if name.lower() in params:
            return {"style": "offset", "param": name, "start": 0,
                    "limit_param": next((l for l in ("limit", "count", "size", "per_page")
                                         if l in params), "limit"),
                    "limit": 20}
    for name in ("cursor", "after", "next", "next_cursor"):
        if name.lower() in params:
            return {"style": "cursor", "param": name}
    return {"style": "unknown"}
