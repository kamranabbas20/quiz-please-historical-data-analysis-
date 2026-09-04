"""Parse a single ``/game/<uuid>`` page into a raw field dict.

Precedence is deliberate: embedded/hydration JSON first (it is the data the
page itself was built from), then schema.org JSON-LD, then OpenGraph meta,
and only then text heuristics over the rendered DOM.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urljoin

from . import extract

log = logging.getLogger(__name__)

RESULTS_LINK_RE = re.compile(r"(результат|рейтинг|протокол|result|rating|nəticə)", re.I)


def parse(html: str, url: str) -> dict[str, Any]:
    """Merge every available representation of the game on this page."""
    game_id = None
    match = extract.GAME_URL_RE.search(url)
    if match:
        game_id = match.group(1)

    layers: list[dict[str, Any]] = []

    # -- text heuristics (lowest precedence) ------------------------------
    labelled = extract.labelled_values(html)
    if labelled:
        layers.append({k: v for k, v in labelled.items()})

    # -- OpenGraph / meta -------------------------------------------------
    meta = extract.meta_tags(html)
    if meta:
        layers.append({
            "title": meta.get("og:title") or meta.get("twitter:title"),
            "description": meta.get("og:description") or meta.get("description"),
            "image_url": meta.get("og:image") or meta.get("twitter:image"),
            "game_url": meta.get("og:url"),
        })

    # -- JSON-LD ----------------------------------------------------------
    for block in extract.json_ld_blocks(html):
        for node in extract.walk(block):
            if not isinstance(node, dict):
                continue
            types = node.get("@type")
            types = [types] if isinstance(types, str) else (types or [])
            if not any("event" in str(t).lower() for t in types):
                continue
            location = node.get("location") or {}
            address = location.get("address") if isinstance(location, dict) else None
            if isinstance(address, dict):
                address = ", ".join(
                    str(address[k]) for k in
                    ("streetAddress", "addressLocality", "addressRegion")
                    if address.get(k)
                )
            offers = node.get("offers") or {}
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            layers.append({
                "title": node.get("name"),
                "description": node.get("description"),
                "date": node.get("startDate"),
                "start_time": node.get("startDate"),
                "venue": location.get("name") if isinstance(location, dict) else location,
                "venue_address": address,
                "price": offers.get("price") if isinstance(offers, dict) else None,
                "currency": offers.get("priceCurrency") if isinstance(offers, dict) else None,
                "image_url": _first(node.get("image")),
                "status": node.get("eventStatus"),
                "game_url": node.get("url"),
                "duration": node.get("duration"),
            })

    # -- hydration / inline JSON (highest precedence) ---------------------
    blobs = extract.hydration_blobs(html) + extract.inline_json_blobs(html)
    best_node: dict | None = None
    for blob in blobs:
        for node in extract.find_game_nodes(blob):
            node_id = extract.pick(node, "game_id")
            if game_id and str(node_id) == game_id:
                best_node = node
                break
            if best_node is None:
                best_node = node
        if best_node and game_id and str(extract.pick(best_node, "game_id")) == game_id:
            break
    if best_node:
        layers.append(from_node(best_node))

    merged: dict[str, Any] = {}
    for layer in layers:  # later layers win
        for key, value in layer.items():
            if value not in (None, "", [], {}):
                merged[key] = value

    merged.setdefault("game_id", game_id)
    merged["game_url"] = url
    merged.setdefault("results_url", _results_link(html, url))
    merged.setdefault("image_url", _first_image(html, url))
    merged["_raw_node"] = best_node
    merged["_source"] = merged.get("_source", "game_page")
    return merged


def from_node(node: dict) -> dict[str, Any]:
    """Project an arbitrary game-shaped JSON dict onto the dataset fields."""
    out: dict[str, Any] = {}
    for field in extract.FIELD_ALIASES:
        value = extract.pick(node, field)
        if value not in (None, "", [], {}):
            out[field] = value
    return out


def _first(value: Any) -> Any:
    if isinstance(value, list):
        return value[0] if value else None
    if isinstance(value, dict):
        return value.get("url") or value.get("contentUrl")
    return value


def _results_link(html: str, base: str) -> str | None:
    soup = extract.soup_of(html)
    for a in soup.find_all("a", href=True):
        text = a.get_text(" ", strip=True)
        if RESULTS_LINK_RE.search(text) or RESULTS_LINK_RE.search(a["href"]):
            return urljoin(base, a["href"])
    return None


def _first_image(html: str, base: str) -> str | None:
    soup = extract.soup_of(html)
    for img in soup.find_all("img", src=True):
        src = img["src"]
        if src.startswith("data:"):
            continue
        return urljoin(base, src)
    return None
