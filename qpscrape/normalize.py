"""Normalisation of raw scraped values into the dataset schema."""

from __future__ import annotations

import html as html_mod
import re
from datetime import datetime, date as date_cls
from typing import Any

MONTHS: dict[str, int] = {}
for _names, _num in [
    (("january", "jan", "января", "январь", "янв", "yanvar", "yan"), 1),
    (("february", "feb", "февраля", "февраль", "фев", "fevral", "fev"), 2),
    (("march", "mar", "марта", "март", "мар", "mart"), 3),
    (("april", "apr", "апреля", "апрель", "апр", "aprel"), 4),
    (("may", "мая", "май", "may"), 5),
    (("june", "jun", "июня", "июнь", "июн", "iyun"), 6),
    (("july", "jul", "июля", "июль", "июл", "iyul"), 7),
    (("august", "aug", "августа", "август", "авг", "avqust", "avq"), 8),
    (("september", "sep", "sept", "сентября", "сентябрь", "сен", "sentyabr", "sent"), 9),
    (("october", "oct", "октября", "октябрь", "окт", "oktyabr", "okt"), 10),
    (("november", "nov", "ноября", "ноябрь", "ноя", "noyabr", "noy"), 11),
    (("december", "dec", "декабря", "декабрь", "дек", "dekabr", "dek"), 12),
]:
    for _n in _names:
        MONTHS[_n] = _num

_ISO_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_DMY_RE = re.compile(r"\b(\d{1,2})[./](\d{1,2})[./](\d{2,4})\b")
_TEXT_DATE_RE = re.compile(r"\b(\d{1,2})\s+([^\W\d_]{3,12})\.?\s*(\d{4})?", re.UNICODE)
_ISO_DT_RE = re.compile(r"\d{4}-\d{2}-\d{2}[T ](\d{2}):(\d{2})")
_TIME_RE = re.compile(r"(?<![\d:.])([01]?\d|2[0-3])[:.]([0-5]\d)(?!\d)")
_PRICE_RE = re.compile(r"(\d[\d\s  ]*(?:[.,]\d+)?)")
CURRENCY_HINTS = [
    ("AZN", ("azn", "₼", "ман", "man.")),
    ("RUB", ("rub", "₽", "руб")),
    ("USD", ("usd", "$")),
    ("EUR", ("eur", "€")),
    ("KZT", ("kzt", "₸", "тг")),
]


def clean_text(value: Any) -> str | None:
    """Collapse whitespace and unescape entities; ``None`` for empty results."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        parts = [clean_text(v) for v in value]
        value = " ".join(p for p in parts if p)
    if not isinstance(value, str):
        value = str(value)
    text = html_mod.unescape(re.sub(r"<[^>]+>", " ", value))
    text = re.sub(r"[\s  ]+", " ", text).strip()
    return text or None


def norm_date(value: Any, fallback_year: int | None = None) -> str | None:
    """Best-effort ``YYYY-MM-DD``.

    Handles ISO strings, epoch seconds/millis, ``dd.mm.yyyy`` and textual
    ``12 сентября 2024`` / ``12 sentyabr`` forms.  A date written without a
    year (common on schedule cards) takes ``fallback_year``.
    """
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit() and len(value) >= 9):
        try:
            ts = float(value)
            if ts > 1e11:  # milliseconds
                ts /= 1000.0
            return datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, (datetime, date_cls)):
        return value.strftime("%Y-%m-%d")
    text = clean_text(value) or ""
    if not text:
        return None

    match = _ISO_RE.search(text)
    if match:
        return _safe_ymd(int(match.group(1)), int(match.group(2)), int(match.group(3)))

    match = _DMY_RE.search(text)
    if match:
        day, month, year = (int(g) for g in match.groups())
        if year < 100:
            year += 2000
        if month > 12 >= day:  # written mm/dd
            day, month = month, day
        return _safe_ymd(year, month, day)

    match = _TEXT_DATE_RE.search(text.lower())
    if match:
        day = int(match.group(1))
        month = MONTHS.get(match.group(2).strip("."))
        year = int(match.group(3)) if match.group(3) else fallback_year
        if month and year:
            return _safe_ymd(year, month, day)
    return None


def _safe_ymd(year: int, month: int, day: int) -> str | None:
    try:
        return date_cls(year, month, day).isoformat()
    except ValueError:
        return None


def norm_time(value: Any) -> str | None:
    """Best-effort ``HH:MM``."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return None
    if isinstance(value, datetime):
        return value.strftime("%H:%M")
    text = clean_text(value) or ""
    # An ISO timestamp must be read as a timestamp: a bare time regex would
    # otherwise latch onto the seconds field ("...T19:00:00" -> "00:00").
    match = _ISO_DT_RE.search(text)
    if match:
        return f"{int(match.group(1)):02d}:{match.group(2)}"
    match = _TIME_RE.search(text)
    if not match:
        return None
    return f"{int(match.group(1)):02d}:{match.group(2)}"


def norm_price(value: Any) -> tuple[float | None, str | None]:
    """Return ``(amount, currency)``; either component may be ``None``."""
    if value in (None, ""):
        return None, None
    currency = None
    if isinstance(value, (int, float)):
        return float(value), None
    text = clean_text(value) or ""
    low = text.lower()
    for code, hints in CURRENCY_HINTS:
        if any(h in low for h in hints):
            currency = code
            break
    match = _PRICE_RE.search(text.replace(",", "."))
    if not match:
        return None, currency
    number = re.sub(r"[\s ]", "", match.group(1))
    try:
        amount = float(number)
    except ValueError:
        return None, currency
    return amount, currency


def norm_int(value: Any) -> int | None:
    if value in (None, "", []):
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    match = re.search(r"\d+", str(value))
    return int(match.group(0)) if match else None


def norm_duration_minutes(value: Any) -> int | None:
    """Parse ``2 часа``/``2h 30m``/``150`` into whole minutes."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = (clean_text(value) or "").lower()
    hours = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:ч|час|hour|h|saat)", text)
    minutes = re.search(r"(\d+)\s*(?:м|мин|min|m|dəq)", text)
    total = 0.0
    if hours:
        total += float(hours.group(1).replace(",", ".")) * 60
    if minutes:
        total += float(minutes.group(1))
    if total:
        return int(round(total))
    plain = norm_int(text)
    return plain


def norm_game_number(value: Any) -> str | None:
    """Keep the printed form (``#12``, ``12.3``) -- it is an identifier, not a count."""
    text = clean_text(value)
    if not text:
        return None
    match = re.search(r"#?\s*(\d+(?:[.,]\d+)?)", text)
    return match.group(1).replace(",", ".") if match else text


def norm_url(value: Any, base: str = "https://baku.quizplease.com") -> str | None:
    from urllib.parse import urljoin

    text = clean_text(value)
    if not text:
        return None
    if text.startswith(("http://", "https://")):
        return text
    if text.startswith("//"):
        return "https:" + text
    if text.startswith("/"):
        return urljoin(base, text)
    return text
