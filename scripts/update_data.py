#!/usr/bin/env python3
"""
ReelMetrics — automated box-office and audience-data updater.

Design goals
------------
* Track worldwide theatrical collections on a day-wise operational basis.
* Preserve the existing data.json schema and frontend compatibility.
* Reject suspicious parses and stale publisher snapshots.
* Keep the last good value when a source fails or omits a metric.
* Update reviews.json independently; TMDB failure never blocks box-office data.
* Write JSON atomically so an interrupted run cannot corrupt the files.

Environment:
    TMDB_READ_ACCESS_TOKEN  (optional for box-office ingestion; required for TMDB)
    TMDB_MOVIE_ID           (optional; defaults to the configured project movie ID)
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = ROOT / "data.json"
REVIEWS_FILE = ROOT / "reviews.json"

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc

USER_AGENT = (
    "Mozilla/5.0 (compatible; ReelMetrics/3.0; "
    "+https://github.com)"
)
HTTP_TIMEOUT_SECONDS = 25
MAX_HTML_BYTES = 8_000_000

RELEASE_DATE = "2026-11-05"
TMDB_MOVIE_ID = 656908
TMDB_API_BASE = "https://themoviedb.org"
TMDB_REVIEW_PAGES_MAX = 3
TMDB_REVIEW_TEXT_MAX_CHARS = 5000

# Direct target tracking URL for daily theatrical collections
LIVE_BOX_OFFICE_URL = "https://sacnilk.com"

SOURCES = [
    {
        "name": "Sacnilk",
        "tier": "primary",
        "url": "https://sacnilk.com",
        "role": "India & Worldwide day-wise live theatrical collection tracker",
    },
    {
        "name": "The Movie Database (TMDB)",
        "tier": "primary",
        "url": "https://themoviedb.org",
        "role": "Audience metrics and user reviews verification platform",
    }
]


# ---------------------------------------------------------------------------
# GENERAL UTILITIES
# ---------------------------------------------------------------------------

def now_ist() -> str:
    return datetime.now(IST).isoformat(timespec="seconds")


def parse_datetime(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None

    raw = value.strip()
    candidates = [raw, raw.replace(" IST", "+05:30")]

    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=IST)
            return parsed.astimezone(UTC)
        except ValueError:
            pass

    for fmt in (
        "%Y-%m-%d %H:%M IST",
        "%Y-%m-%d %H:%M",
        "%d %b %Y %H:%M",
        "%d %B %Y %H:%M",
    ):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=IST).astimezone(UTC)
        except ValueError:
            continue

    return None


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("JSON root must be an object")
        return value
    except Exception as exc:
        raise RuntimeError(f"Cannot read {path.name}: {exc}") from exc


def fetch_bytes(url: str, accept: str = "text/html,application/json") -> bytes:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status} for {url}")
        body = response.read(MAX_HTML_BYTES + 1)
        if len(body) > MAX_HTML_BYTES:
            raise RuntimeError(f"Response too large: {url}")
        return body


def fetch_page(url: str) -> str:
    return fetch_bytes(url).decode("utf-8", errors="replace")


def fetch_json(url: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
    request_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        request_headers.update(headers)
    request = Request(url, headers=request_headers)

    with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        if response.status != 200:
            raise RuntimeError("HTTP error for API request")
        payload = json.loads(response.read().decode("utf-8", errors="replace"))

    if not isinstance(payload, dict):
        raise RuntimeError("API returned an unexpected JSON structure")
    return payload


# ---------------------------------------------------------------------------
# HTML TEXT EXTRACTION
# ---------------------------------------------------------------------------

class VisibleTextParser(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in self.SKIP:
            self.skip_stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self.skip_stack:
            index = len(self.skip_stack) - 1 - self.skip_stack[::-1].index(tag)
            del self.skip_stack[index:]

    def handle_data(self, data: str) -> None:
        if not self.skip_stack and data.strip():
            self.parts.append(data.strip())


def visible_text(raw_html: str) -> str:
    parser = VisibleTextParser()
    parser.feed(raw_html)
    return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()


def clean_number(value: str) -> float | None:
    try:
        return float(value.replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def extract_publisher_timestamp(text: str) -> str | None:
    patterns = [
        r"(?:last\s+updated|updated\s+at|updated)\s*[:\-]?\s*(\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2}(?:\s*IST)?)",
        r"(?:last\s+updated|updated\s+at|updated)\s*[:\-]?\s*(\d{1,2}\s+[A-Za-z]+\s+\d{4}\s+\d{1,2}:\d{2})",
        r"Updated\s+(\d{1,2}:\d{2}\s*IST)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return match.group(1).strip()
    return None


def merge_non_null(previous: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    merged = dict(previous)
    for key, value in candidate.items():
        if value is not None:
            merged[key] = value
    return merged


# ---------------------------------------------------------------------------
# DAYWISE WORLDWIDE COLLECTION PARSER ENGINE
# ---------------------------------------------------------------------------

def extract_daywise_worldwide_collections(raw_html: str) -> dict[str, dict[str, Any]]:
    """
    Parses the live tracker rows to explicitly extract daily worldwide gross records.
    """
    text = visible_text(raw_html)
    clean_text = re.sub(r'\s+', ' ', text)
    
    # Matches patterns like: Day 1 Worldwide Gross: 125.40 Cr, Day 2 WW Gross: 92.10 Cr
    day_regex = r'(?:Day\s*(\d+)).*?(?:Worldwide|WW).*?([0-9,]+(?:\.[0-9]+)?)\s*(?:Cr|Crore)'
    matches = re.findall(day_regex, clean_text, re.IGNORECASE)
    
    day_wise_collection = {}
    base_date = datetime.strptime(RELEASE_DATE, "%Y-%m-%d")
    
    for day_str, gross_str in matches:
        day_num = int(day_str)
        gross_value = clean_number(gross_str)
        
        if gross_value and 0 < gross_value < 3000:
            target_delta = day_num - 1
            target_date = (base_date + timedelta(days=target_delta)).strftime("%Y-%m-%d")
            
            day_wise_collection[target_date] = {
                "date": target_date,
                "day": day_num,
                "worldwideGrossCr": round(gross_value, 2),
                "scope": "single_day_theatrical_collection",
                "currency": "INR",
                "fetchedAt": now_ist()
            }
            
    return day_wise_collection


# ---------------------------------------------------------------------------
# TMDB AUDIENCE DATA
# ---------------------------------------------------------------------------

def tmdb_request(path: str, token: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    query = f"?{urlencode(params)}" if params else ""
    return fetch_json(f"{TMDB_API_BASE}{path}{query}", headers={"Authorization": f"Bearer {token}"})


def fetch_tmdb_audience_data() -> dict[str, Any]:
    token = os.getenv("TMDB_READ_ACCESS_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TMDB_READ_ACCESS_TOKEN is not configured")

    movie_id_raw = os.getenv("TMDB_MOVIE_ID", str(TMDB_MOVIE_ID)).strip()
    if not movie_id_raw.isdigit() or int(movie_id_raw) <= 0:2
