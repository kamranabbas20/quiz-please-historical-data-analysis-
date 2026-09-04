"""The independent ways a game can be discovered, unioned by the pipeline.

No single source is trusted to be complete: the archive endpoint may cap at a
recent window, the DOM may lazy-load, and the sitemap may retain URLs the
listing has dropped.  Every source therefore runs, and the results are merged.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse

from . import extract
from .http import Fetcher

log = logging.getLogger(__name__)

BASE = "https://baku.quizplease.com"
ARCHIVE_PATHS = [
    "/schedule-past", "/schedule", "/games", "/results", "/rating",
    "/photos", "/photo", "/archive",
]
MAX_PAGES = 500


# ---------------------------------------------------------------------------
# 1. Backend API (preferred)
# ---------------------------------------------------------------------------
def from_api(config: dict[str, Any], fetcher: Fetcher) -> list[dict]:
    """Page through the endpoint discovered by :mod:`qpscrape.discover`."""
    api = (config or {}).get("api")
    if not api or not api.get("url"):
        log.info("no API endpoint configured -- skipping API source")
        return []

    url = api["url"]
    method = (api.get("method") or "GET").upper()
    base_params = dict(api.get("query_params") or {})
    headers = dict(api.get("headers") or {})
    pag = api.get("pagination") or {"style": "unknown"}
    style = pag.get("style", "unknown")

    records: list[dict] = []
    seen_ids: set[str] = set()
    cursor: Any = None
    page_index = pag.get("start", 1 if style == "page" else 0)
    limit = pag.get("limit", 20)

    for _ in range(MAX_PAGES):
        params = dict(base_params)
        if style == "page":
            params[pag["param"]] = page_index
        elif style == "offset":
            params[pag["param"]] = page_index
            params[pag.get("limit_param", "limit")] = limit
        elif style == "cursor":
            if cursor is not None:
                params[pag["param"]] = cursor
            elif page_index != pag.get("start", 0):
                break

        resp = fetcher.request(method, url, params=params, headers=headers or None)
        if resp is None or resp.status_code >= 400:
            log.warning("API page %s returned %s -- stopping",
                        page_index, getattr(resp, "status_code", "error"))
            break
        try:
            body = resp.json()
        except ValueError:
            log.warning("API page %s was not JSON -- stopping", page_index)
            break

        nodes = extract.find_game_nodes(body)
        fresh = [n for n in nodes if _node_key(n) not in seen_ids]
        for node in fresh:
            seen_ids.add(_node_key(node))
            node["_source"] = "api"
            records.append(node)
        log.info("API page %s: %d records (%d new, %d total)",
                 page_index, len(nodes), len(fresh), len(records))

        if not fresh:
            break
        cursor = _next_cursor(body)
        if style == "cursor":
            if cursor is None or not _has_more(body):
                break
        elif style == "page":
            page_index += pag.get("step", 1)
            if not _has_more(body, len(nodes), limit):
                break
        elif style == "offset":
            page_index += limit
            if not _has_more(body, len(nodes), limit):
                break
        else:  # unknown pagination: a single call is all we can justify
            break
    return records


def _node_key(node: dict) -> str:
    for field in ("game_id", "game_url", "title"):
        value = extract.pick(node, field)
        if value:
            return f"{field}:{value}"
    return repr(sorted(node.items()))[:200]


def _next_cursor(body: Any) -> Any:
    for node in extract.walk(body):
        if isinstance(node, dict):
            for key in ("next_cursor", "nextCursor", "cursor", "next", "next_page",
                        "nextPage", "after"):
                if key in node and node[key] not in (None, "", False):
                    return node[key]
    return None


def _has_more(body: Any, returned: int | None = None, limit: int | None = None) -> bool:
    """Trust an explicit flag when present, else fall back to a full page."""
    for node in extract.walk(body):
        if isinstance(node, dict):
            for key in ("has_more", "hasMore", "has_next", "hasNext", "more"):
                if key in node and isinstance(node[key], bool):
                    return node[key]
    if returned is not None and limit:
        return returned >= limit
    return bool(returned)


# ---------------------------------------------------------------------------
# 2. Sitemap (catches years the listing no longer links)
# ---------------------------------------------------------------------------
def from_sitemap(fetcher: Fetcher, base: str = BASE) -> list[str]:
    """Every ``/game/<uuid>`` URL reachable from robots.txt + sitemap indexes."""
    roots = []
    robots = fetcher.get_text(urljoin(base, "/robots.txt"))
    if robots:
        roots += re.findall(r"(?i)^\s*sitemap:\s*(\S+)", robots, re.M)
    roots += [urljoin(base, p) for p in
              ("/sitemap.xml", "/sitemap_index.xml", "/sitemap-index.xml")]

    seen_maps: set[str] = set()
    game_urls: dict[str, None] = {}
    queue = list(dict.fromkeys(roots))
    while queue:
        sm_url = queue.pop(0)
        if sm_url in seen_maps:
            continue
        seen_maps.add(sm_url)
        body = fetcher.get_text(sm_url)
        if not body:
            continue
        locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", body, re.I)
        for loc in locs:
            if re.search(r"\.xml(\.gz)?$", loc, re.I):
                queue.append(loc)
            elif extract.GAME_URL_RE.search(loc):
                game_urls.setdefault(loc.strip(), None)
        log.info("sitemap %s -> %d locs (%d game urls so far)",
                 sm_url, len(locs), len(game_urls))
    return list(game_urls)


# ---------------------------------------------------------------------------
# 3. Static archive pages + numeric pagination probing
# ---------------------------------------------------------------------------
def from_static_pages(fetcher: Fetcher, base: str = BASE,
                      paths: Iterable[str] = ARCHIVE_PATHS) -> list[str]:
    """Server-rendered archive pages, including ``?page=N`` walks."""
    urls: dict[str, None] = {}
    for path in paths:
        page_url = urljoin(base, path)
        html = fetcher.get_text(page_url)
        if not html:
            continue
        found = extract.game_links(html, base)
        for u in found:
            urls.setdefault(u, None)
        log.info("%s -> %d game links", path, len(found))
        if not found:
            continue
        # Walk ?page=N while it keeps producing links we have not seen.
        for page in range(2, MAX_PAGES):
            paged = fetcher.get_text(page_url, params={"page": page})
            if not paged:
                break
            new = [u for u in extract.game_links(paged, base) if u not in urls]
            if not new:
                break
            for u in new:
                urls.setdefault(u, None)
            log.info("%s?page=%d -> %d new (total %d)", path, page, len(new), len(urls))
    return list(urls)


# ---------------------------------------------------------------------------
# 4. Rendered DOM (last resort when the archive is client-side only)
# ---------------------------------------------------------------------------
def from_rendered_dom(url: str = f"{BASE}/schedule-past", headless: bool = True,
                      max_scrolls: int = 400) -> list[str]:
    """Drive the browser purely to harvest ``/game/`` links after full scroll."""
    from .discover import _click_load_more, _executable_path, SCROLL_PAUSE_MS
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        launch: dict[str, Any] = {"headless": headless}
        exe = _executable_path()
        if exe:
            launch["executable_path"] = exe
        browser = p.chromium.launch(**launch)
        page = browser.new_context(locale="ru-RU").new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        previous, stagnant = -1, 0
        for _ in range(max_scrolls):
            count = page.locator("a[href*='/game/']").count()
            stagnant = stagnant + 1 if count == previous else 0
            if stagnant >= 2:
                break
            previous = count
            _click_load_more(page)
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(SCROLL_PAUSE_MS)
        html = page.content()
        browser.close()
    links = extract.game_links(html, url)
    log.info("rendered DOM -> %d game links", len(links))
    return links


def game_id_of(url: str) -> str | None:
    match = extract.GAME_URL_RE.search(urlparse(url).path)
    return match.group(1) if match else None
