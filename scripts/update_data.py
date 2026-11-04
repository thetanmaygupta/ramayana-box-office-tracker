#!/usr/bin/env python3
"""
ReelMetrics — automated box-office and audience-data updater.

Design goals
------------
* Preserve the existing data.json schema and frontend compatibility.
* Keep advance bookings separate from actual theatrical collections.
* Keep Day 1, Day 2, Day 3, and all-dates scopes separate.
* Never manufacture missing figures or combine mismatched scopes.
* Reject suspicious parses and stale publisher snapshots.
* Keep the last good value when a source fails or omits a metric.
* Update reviews.json independently; TMDB failure never blocks box-office data.
* Write JSON atomically so an interrupted run cannot corrupt the files.

Environment:
    TMDB_READ_ACCESS_TOKEN  (optional for box-office ingestion; required for TMDB)

No third-party Python packages are required.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
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
    "+https://github.com/thetanmaygupta/ramayana-box-office-tracker)"
)
HTTP_TIMEOUT_SECONDS = 25
MAX_HTML_BYTES = 8_000_000

RELEASE_DATE = "2026-11-05"
TMDB_MOVIE_ID = 656908
TMDB_API_BASE = "https://api.themoviedb.org/3"
TMDB_REVIEW_PAGES_MAX = 3
TMDB_REVIEW_TEXT_MAX_CHARS = 5000

BOI24_DAILY_URLS = {
    "2026-11-05": (
        "https://www.boxofficeindia24.com/quicknews/"
        "ramayana-day-1-advance-booking-20261105"
    ),
    "2026-11-06": (
        "https://www.boxofficeindia24.com/quicknews/"
        "ramayana-day-2-advance-booking-20261106"
    ),
    "2026-11-07": (
        "https://www.boxofficeindia24.com/quicknews/"
        "ramayana-day-3-advance-booking-20261107"
    ),
}
BOI24_OVERSEAS_URL = (
    "https://www.boxofficeindia24.com/quicknews/"
    "ramayana-day-1-advance-booking-20261105?view=overseas"
)

SACNILK_LIVE_URL = (
    "https://www.sacnilk.com/livetracker/"
    "ramayana_part_1_2026_Advance-Booking-Live"
)
SACNILK_DAY1_URL = (
    "https://www.sacnilk.com/quicknews/"
    "ramayana_part_1_2026_First_Day_Advance_Booking_Report"
)
SACNILK_ALL_DATES_URL = (
    "https://www.sacnilk.com/quicknews/"
    "ramayana_part_1_2026_Overall_Advance_Booking_Report"
)

SOURCES = [
    {
        "name": "Sacnilk",
        "tier": "primary",
        "url": "https://www.sacnilk.com/",
        "role": "India advance-booking tracker and cross-check",
    },
    {
        "name": "BoxOfficeIndia24",
        "tier": "primary",
        "url": "https://www.boxofficeindia24.com/",
        "role": "Day-wise India and overseas advance-booking reports",
    },
    {
        "name": "The Week",
        "tier": "secondary",
        "url": "https://www.theweek.in/",
        "role": "Editorial validation; not an independent tracker when citing primary data",
    },
    {
        "name": "Financial Express",
        "tier": "secondary",
        "url": "https://www.financialexpress.com/",
        "role": "Editorial validation; not an independent tracker when citing primary data",
    },
    {
        "name": "Koimoi",
        "tier": "secondary",
        "url": "https://www.koimoi.com/",
        "role": "Trade-report validation; not an independent tracker when citing primary data",
    },
]


# ---------------------------------------------------------------------------
# GENERAL UTILITIES
# ---------------------------------------------------------------------------

def now_ist() -> str:
    return datetime.now(IST).isoformat(timespec="seconds")


def parse_datetime(value: Any) -> datetime | None:
    """Parse ISO timestamps and common publisher timestamp formats."""
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
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
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
    request = Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": accept},
    )
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
    request_headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }
    if headers:
        request_headers.update(headers)
    request = Request(url, headers=request_headers)
    with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status} for API request")
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
            # Remove the most recent matching skipped element.
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


def find_number(patterns: list[str], text: str) -> float | None:
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            value = clean_number(match.group(1))
            if value is not None:
                return value
    return None


def find_integer(patterns: list[str], text: str) -> int | None:
    value = find_number(patterns, text)
    return int(value) if value is not None else None


def extract_publisher_timestamp(text: str) -> str | None:
    patterns = [
        r"(?:last\s+updated|updated\s+at|updated)\s*[:\-]?\s*"
        r"(\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2}(?:\s*IST)?)",
        r"(?:last\s+updated|updated\s+at|updated)\s*[:\-]?\s*"
        r"(\d{1,2}\s+[A-Za-z]+\s+\d{4}\s+\d{1,2}:\d{2})",
        r"Updated\s+(\d{1,2}:\d{2}\s*IST)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return match.group(1).strip()
    return None


def is_newer_or_equal(candidate: dict[str, Any], previous: dict[str, Any]) -> bool:
    """Reject older source snapshots when both publisher timestamps are parseable."""
    new_time = parse_datetime(candidate.get("sourceUpdatedText"))
    old_time = parse_datetime(previous.get("sourceUpdatedText"))
    if new_time and old_time:
        return new_time >= old_time
    # If a publisher timestamp is absent, don't replace an existing timestamped record.
    if old_time and not new_time:
        return False
    return True


def merge_non_null(previous: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    merged = dict(previous)
    for key, value in candidate.items():
        if value is not None:
            merged[key] = value
    return merged


# ---------------------------------------------------------------------------
# BOXOFFICEINDIA24 PARSERS
# ---------------------------------------------------------------------------

def extract_metrics(text: str) -> dict[str, Any]:
    return {
        "tickets": find_integer([
            r"gross\s+from\s+([\d,]+)\s+tickets?",
            r"([\d,]+)\s+tickets?\b",
        ], text),
        "shows": find_integer([
            r"across\s+([\d,]+)\s+shows?",
            r"([\d,]+)\s+shows?\b",
        ], text),
        "occupancyPct": find_number([
            r"occupancy(?:\s+rate)?\s*[:\-]?\s*([\d,.]+)\s*%",
        ], text),
    }


def extract_india_gross(text: str) -> float | None:
    return find_number([
        r"\bIndia\s*:\s*₹?\s*([\d,]+(?:\.\d+)?)\s*(?:Cr|Crore)\b",
        r"\bIndia\s+(?:gross|Day\s*\d+\s+gross)\s*[:\-]?\s*₹?\s*"
        r"([\d,]+(?:\.\d+)?)\s*(?:Cr|Crore)\b",
    ], text)


def extract_worldwide_gross(text: str) -> float | None:
    return find_number([
        r"\bWorldwide\s*:\s*₹?\s*([\d,]+(?:\.\d+)?)\s*(?:Cr|Crore)\b",
        r"\bWorldwide\s+(?:advance\s+booking\s+)?(?:gross\s*)?[:\-]?\s*₹?\s*"
        r"([\d,]+(?:\.\d+)?)\s*(?:Cr|Crore)\b",
    ], text)


def extract_overseas_gross(text: str) -> float | None:
    return find_number([
        r"\bOverseas\s*:\s*₹?\s*([\d,]+(?:\.\d+)?)\s*(?:Cr|Crore)\b",
        r"\bOverseas\s+(?:advance\s+booking\s+)?(?:gross\s*)?[:\-]?\s*₹?\s*"
        r"([\d,]+(?:\.\d+)?)\s*(?:Cr|Crore)\b",
    ], text)


def parse_boi24_day(raw_html: str, date: str, day: int, url: str) -> dict[str, Any]:
    text = visible_text(raw_html)
    if "ramayana" not in text.casefold():
        raise RuntimeError("Page title/content did not identify Ramayana")

    india = extract_india_gross(text)
    if india is None:
        raise RuntimeError("Could not identify a labelled India gross figure")
    if not 0 < india < 1000:
        raise RuntimeError("India gross failed range validation")

    worldwide = extract_worldwide_gross(text)
    overseas = extract_overseas_gross(text)

    if worldwide is not None and worldwide < india:
        raise RuntimeError("Worldwide gross is lower than India gross")
    if overseas is not None and overseas > 1000:
        raise RuntimeError("Overseas gross failed range validation")
    if worldwide is not None and overseas is not None:
        if abs(worldwide - (india + overseas)) > 0.20:
            raise RuntimeError("Worldwide, India and overseas values do not reconcile")

    result: dict[str, Any] = {
        "date": date,
        "day": day,
        "indiaGrossCr": round(india, 2),
        "sourceName": "BoxOfficeIndia24",
        "sourceUrl": url,
        "sourceUpdatedText": extract_publisher_timestamp(text),
        "fetchedAt": now_ist(),
        "scope": "single_day_advance",
        "currency": "INR",
    }
    result.update({k: v for k, v in extract_metrics(text).items() if v is not None})
    if worldwide is not None:
        result["worldwideGrossCr"] = round(worldwide, 2)
    if overseas is not None:
        result["overseasGrossCr"] = round(overseas, 2)
    result["worldwideVerifiedFromPublisher"] = (
        worldwide is not None and overseas is not None
    )
    return result


def parse_boi24_overseas(raw_html: str) -> dict[str, Any]:
    text = visible_text(raw_html)
    # Anchor the amount to the OVERSEAS ADVANCE label; never use an arbitrary first currency value.
    gross = find_number([
        r"OVERSEAS\s+ADVANCE[^₹]{0,180}₹\s*([\d,]+(?:\.\d+)?)\s*Cr",
    ], text)
    if gross is None or not 0 < gross < 1000:
        raise RuntimeError("Could not validate the overseas all-dates gross")

    return {
        "grossCr": round(gross, 2),
        "tickets": find_integer([r"gross\s+from\s+([\d,]+)\s+tickets?"], text),
        "shows": find_integer([r"([\d,]+)\s+SHOWS"], text),
        "cinemas": find_integer([r"([\d,]+)\s+CINEMAS"], text),
        "countries": find_integer([r"([\d,]+)\s+COUNTRIES"], text),
        "occupancyPct": find_number([r"([\d.]+)\s*%\s*OCCUPANCY"], text),
        "dateRange": (
            re.search(
                r"OVERSEAS\s+ADVANCE\s*[·|]\s*(\d{1,2}\s*[–-]\s*\d{1,2}\s+[A-Za-z]+)",
                text, re.I
            ).group(1)
            if re.search(
                r"OVERSEAS\s+ADVANCE\s*[·|]\s*(\d{1,2}\s*[–-]\s*\d{1,2}\s+[A-Za-z]+)",
                text, re.I
            ) else None
        ),
        "sourceName": "BoxOfficeIndia24",
        "sourceUrl": BOI24_OVERSEAS_URL,
        "sourceUpdatedText": extract_publisher_timestamp(text),
        "fetchedAt": now_ist(),
        "scope": "overseas_all_dates",
        "currency": "INR",
        "note": "Overseas all-dates advance booking; not worldwide Day 1 and not actual collection.",
    }


# ---------------------------------------------------------------------------
# SACNILK PARSING — conservative and labelled-field only
# ---------------------------------------------------------------------------

def extract_sacnilk_labeled_gross(text: str, labels: tuple[str, ...]) -> float | None:
    """
    Extract only amounts explicitly adjacent to a requested label.
    Deliberately avoids generic first-number parsing on pages with many figures.
    """
    for label in labels:
        patterns = [
            rf"\b{label}\b\s*(?:gross|collection|advance booking)?\s*[:\-]?\s*₹?\s*"
            rf"([\d,]+(?:\.\d+)?)\s*(?:Cr|Crore)\b",
            rf"₹\s*([\d,]+(?:\.\d+)?)\s*(?:Cr|Crore)\s*(?:{label})\b",
        ]
        value = find_number(patterns, text)
        if value is not None:
            return value
    return None


def parse_sacnilk_page(raw_html: str, url: str, scope: str) -> dict[str, Any]:
    text = visible_text(raw_html)
    if "ramayana" not in text.casefold():
        raise RuntimeError("Sacnilk page did not identify Ramayana")

    india = extract_sacnilk_labeled_gross(
        text, ("India", "India Gross", "India Net")
    )
    worldwide = extract_sacnilk_labeled_gross(
        text, ("Worldwide", "Worldwide Gross")
    )
    overseas = extract_sacnilk_labeled_gross(
        text, ("Overseas", "Overseas Gross")
    )

    # A Sacnilk page can contain multiple unrelated totals. Accept it only
    # when a clearly labelled India figure is found.
    if india is None or not 0 < india < 1000:
        raise RuntimeError(
            "No unambiguous labelled India gross found on Sacnilk; previous snapshot retained"
        )

    if worldwide is not None and worldwide < india:
        raise RuntimeError("Sacnilk worldwide figure is below India figure")

    result: dict[str, Any] = {
        "indiaGrossCr": round(india, 2),
        "sourceName": "Sacnilk",
        "sourceUrl": url,
        "sourceUpdatedText": extract_publisher_timestamp(text),
        "fetchedAt": now_ist(),
        "scope": scope,
        "currency": "INR",
        "parsePolicy": "labelled_values_only",
    }
    if worldwide is not None:
        result["worldwideGrossCr"] = round(worldwide, 2)
    if overseas is not None:
        result["overseasGrossCr"] = round(overseas, 2)
    return result


# ---------------------------------------------------------------------------
# TMDB AUDIENCE DATA
# ---------------------------------------------------------------------------

def tmdb_request(path: str, token: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    query = f"?{urlencode(params)}" if params else ""
    return fetch_json(
        f"{TMDB_API_BASE}{path}{query}",
        headers={"Authorization": f"Bearer {token}"},
    )


def fetch_tmdb_audience_data() -> dict[str, Any]:
    token = os.getenv("TMDB_READ_ACCESS_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TMDB_READ_ACCESS_TOKEN is not configured")

    movie = tmdb_request(
        f"/movie/{TMDB_MOVIE_ID}", token, {"language": "en-US"}
    )
    title = str(movie.get("title") or "").strip()
    original_title = str(movie.get("original_title") or "").strip()
    if "ramayana" not in f"{title} {original_title}".casefold():
        raise RuntimeError("TMDB movie ID/title validation failed")

    average = movie.get("vote_average")
    votes = movie.get("vote_count")
    if not isinstance(average, (int, float)) or not 0 <= float(average) <= 10:
        average = None
    if not isinstance(votes, int) or votes < 0:
        votes = None

    first = tmdb_request(
        f"/movie/{TMDB_MOVIE_ID}/reviews", token,
        {"language": "en-US", "page": 1}
    )
    total_results = max(0, int(first.get("total_results") or 0))
    total_pages = max(0, int(first.get("total_pages") or 0))
    pages = min(total_pages, TMDB_REVIEW_PAGES_MAX)

    reviews: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for page in range(1, pages + 1):
        payload = first if page == 1 else tmdb_request(
            f"/movie/{TMDB_MOVIE_ID}/reviews", token,
            {"language": "en-US", "page": page}
        )
        for item in payload.get("results", []):
            if not isinstance(item, dict):
                continue
            review_id = str(item.get("id") or "")
            if review_id and review_id in seen_ids:
                continue
            content = str(item.get("content") or "").strip()
            if not content:
                continue
            if review_id:
                seen_ids.add(review_id)
            author_details = item.get("author_details") or {}
            rating = author_details.get("rating")
            if not isinstance(rating, (int, float)) or not 0 <= float(rating) <= 10:
                rating = None
            reviews.append({
                "id": item.get("id"),
                "author": item.get("author"),
                "username": author_details.get("username"),
                "rating": rating,
                "createdAt": item.get("created_at"),
                "updatedAt": item.get("updated_at"),
                "url": item.get("url"),
                "content": content[:TMDB_REVIEW_TEXT_MAX_CHARS],
                "contentTruncated": len(content) > TMDB_REVIEW_TEXT_MAX_CHARS,
                "source": "TMDB",
            })

    fetched_at = now_ist()
    return {
        "movie": title or "Ramayana: Rise of a Legend",
        "status": "success",
        "source": "The Movie Database (TMDB)",
        "sourceUrl": f"https://www.themoviedb.org/movie/{TMDB_MOVIE_ID}",
        "lastUpdated": fetched_at,
        # Compatibility with existing 0–100 frontend score display.
        # This is a scaled TMDB average, not a positive-vote percentage.
        "tmdbScore": round(float(average) * 10, 1) if average is not None else None,
        "voteCount": votes,
        "reviewCount": total_results,
        "highlights": [],
        "sentiment": None,
        "reviews": reviews,
        "message": "TMDB rating and written reviews fetched. No sentiment labels are inferred.",
        "tmdb": {
            "movieId": TMDB_MOVIE_ID,
            "title": title,
            "originalTitle": original_title,
            "overview": movie.get("overview"),
            "releaseDate": movie.get("release_date"),
            "voteAverage": round(float(average), 2) if average is not None else None,
            "voteCount": votes,
            "reviewCount": total_results,
            "reviewPagesFetched": pages,
            "reviewPagesAvailable": total_pages,
            "posterPath": movie.get("poster_path"),
            "backdropPath": movie.get("backdrop_path"),
            "genres": [
                g.get("name") for g in movie.get("genres", [])
                if isinstance(g, dict) and g.get("name")
            ],
            "apiBase": TMDB_API_BASE,
        },
        "internalAudit": {
            "tmdbMovieId": TMDB_MOVIE_ID,
            "tmdbReviewCount": total_results,
            "tmdbReviewRecordsFetched": len(reviews),
            "sentimentAnalysis": "not_run",
            "scoreDefinition": "TMDB vote_average multiplied by 10",
        },
    }


def update_reviews() -> dict[str, Any]:
    try:
        payload = fetch_tmdb_audience_data()
        atomic_write_json(REVIEWS_FILE, payload)
        print(
            f"TMDB updated: {payload.get('voteCount')} votes; "
            f"{payload.get('reviewCount')} reviews; "
            f"{len(payload.get('reviews', []))} review records fetched."
        )
        return {
            "status": "success",
            "fetchedAt": payload["lastUpdated"],
            "reviewCount": payload.get("reviewCount", 0),
            "voteCount": payload.get("voteCount"),
        }
    except Exception as exc:
        # Keep the last known-good reviews file untouched.
        print(f"TMDB update failed; reviews.json preserved: {exc}", file=sys.stderr)
        return {
            "status": "failed" if os.getenv("TMDB_READ_ACCESS_TOKEN") else "not_configured",
            "checkedAt": now_ist(),
            "warning": str(exc),
            "preservedPreviousData": REVIEWS_FILE.exists(),
        }


# ---------------------------------------------------------------------------
# DATA MODEL / INGESTION
# ---------------------------------------------------------------------------

def configure_source_policy(data: dict[str, Any]) -> None:
    data["sources"] = SOURCES
    data["sourcePolicy"] = {
        "primary": ["Sacnilk", "BoxOfficeIndia24"],
        "secondary": ["The Week", "Financial Express", "Koimoi"],
        "actualCollections": "Separate dataset. Never combine with advance bookings.",
        "independenceRule": (
            "Secondary publications citing a primary tracker are not independent measurements."
        ),
        "comparisonRule": (
            "Compare matching dates, territories, gross/net definitions, "
            "blocked-seat treatment and publisher timestamps."
        ),
    }
    data.setdefault("actualCollections", {
        "status": "awaiting_verified_trade_reports",
        "records": [],
        "note": "Actual theatrical collections are separate from advance-booking figures.",
    })


def record_source_status(statuses: dict[str, Any], name: str, status: str,
                         url: str, warning: str | None = None) -> None:
    statuses[name] = {
        "status": status,
        "url": url,
        "checkedAt": now_ist(),
        "warning": warning,
    }


def main() -> int:
    data = load_json(DATA_FILE, {})
    if not data:
        raise RuntimeError("data.json is empty or missing; refusing to create a blank dataset")

    original_data = json.loads(json.dumps(data))
    fetched_at = now_ist()
    warnings: list[str] = []
    statuses: dict[str, Any] = {}

    # 1) TMDB is independent of box-office sources.
    audience_status = update_reviews()

    # 2) Day-wise BOI24 snapshots. Failed or older pages never erase good data.
    day_data = data.setdefault("dayWiseAdvance", {})
    for day_number, (date, url) in enumerate(BOI24_DAILY_URLS.items(), start=1):
        try:
            candidate = parse_boi24_day(fetch_page(url), date, day_number, url)
            previous = day_data.get(date, {})
            if not is_newer_or_equal(candidate, previous):
                raise RuntimeError("Publisher snapshot is older than stored snapshot")
            day_data[date] = merge_non_null(previous, candidate)
            record_source_status(statuses, f"boi24_day_{day_number}", "success", url)
            print(f"BOI24 Day {day_number}: updated")
        except Exception as exc:
            warning = f"BOI24 Day {day_number}: {exc}"
            warnings.append(warning)
            record_source_status(statuses, f"boi24_day_{day_number}", "retained_previous", url, str(exc))
            print(warning, file=sys.stderr)

    # 3) BOI24 overseas all-dates snapshot.
    try:
        candidate = parse_boi24_overseas(fetch_page(BOI24_OVERSEAS_URL))
        previous = data.get("overseasAllDates", {})
        if not is_newer_or_equal(candidate, previous):
            raise RuntimeError("Publisher snapshot is older than stored snapshot")
        data["overseasAllDates"] = merge_non_null(previous, candidate)
        record_source_status(statuses, "boi24_overseas_all_dates", "success", BOI24_OVERSEAS_URL)
        print("BOI24 overseas all-dates: updated")
    except Exception as exc:
        warning = f"BOI24 overseas all-dates: {exc}"
        warnings.append(warning)
        record_source_status(statuses, "boi24_overseas_all_dates", "retained_previous",
                             BOI24_OVERSEAS_URL, str(exc))
        print(warning, file=sys.stderr)

    # 4) Sacnilk India all-dates cross-check. Conservative parser: only labelled values.
    sacnilk_candidate = None
    sacnilk_url_used = None
    for url, scope in (
        (SACNILK_ALL_DATES_URL, "india_all_dates"),
        (SACNILK_LIVE_URL, "india_live_tracker"),
        (SACNILK_DAY1_URL, "india_day_1_advance"),
    ):
        try:
            sacnilk_candidate = parse_sacnilk_page(fetch_page(url), url, scope)
            sacnilk_url_used = url
            break
        except Exception as exc:
            warnings.append(f"Sacnilk {scope}: {exc}")
            print(f"Sacnilk {scope} not accepted: {exc}", file=sys.stderr)

    if sacnilk_candidate:
        previous = data.get("sacnilkSnapshot", {})
        if is_newer_or_equal(sacnilk_candidate, previous):
            data["sacnilkSnapshot"] = merge_non_null(previous, sacnilk_candidate)
            record_source_status(statuses, "sacnilk", "success", sacnilk_url_used or SACNILK_LIVE_URL)
        else:
            record_source_status(statuses, "sacnilk", "retained_previous",
                                 sacnilk_url_used or SACNILK_LIVE_URL,
                                 "Older publisher snapshot rejected")
    else:
        record_source_status(statuses, "sacnilk", "unavailable",
                             SACNILK_LIVE_URL, "No unambiguous labelled India gross found")

    # 5) Keep legacy Day 1 feed keys used by the current website.
    feed = data.setdefault("automatedFeed", {})
    day1 = day_data.get(RELEASE_DATE, {})
    if day1:
        feed.update({
            "sourceName": "BoxOfficeIndia24 — Ramayana Day 1",
            "sourceUrl": BOI24_DAILY_URLS[RELEASE_DATE],
            "status": "success",
            "fetchedAt": day1.get("fetchedAt"),
            "sourceUpdatedText": day1.get("sourceUpdatedText"),
            "message": (
                "Day 1 India advance booking. Worldwide is shown only when "
                "the publisher reports a matching Day 1 scope."
            ),
        })
        for key in (
            "indiaGrossCr", "tickets", "shows", "occupancyPct",
            "worldwideGrossCr", "overseasGrossCr",
        ):
            if day1.get(key) is not None:
                feed[key] = day1[key]
    else:
        feed.setdefault("status", "awaiting_verified_source_data")

    # Do not leave stale worldwide fields in the legacy Day 1 feed if the
    # current accepted Day 1 page no longer publishes matching figures.
    if day1:
        for key in ("worldwideGrossCr", "overseasGrossCr"):
            if day1.get(key) is None:
                feed.pop(key, None)

    # 6) Sacnilk and BOI24 all-dates are stored as separate source snapshots.
    # Do not silently sum them: their dates/definitions may differ.
    data["sourceSnapshots"] = {
        "sacnilkIndia": data.get("sacnilkSnapshot"),
        "boxOfficeIndia24Overseas": data.get("overseasAllDates"),
        "note": (
            "Snapshots remain source-scoped. A combined worldwide all-dates "
            "figure is not generated unless dates, territory and gross definitions "
            "are explicitly compatible."
        ),
    }

    configure_source_policy(data)
    data["sourceValidation"] = {
        "sacnilk": statuses.get("sacnilk", {
            "status": "not_checked", "url": SACNILK_LIVE_URL
        }),
        "boxOfficeIndia24": {
            key: value for key, value in statuses.items() if key.startswith("boi24_")
        },
        "secondary": {
            "status": "registered_for_manual_validation",
            "note": "Secondary reports are validation references, not independent measurements when they cite primary trackers.",
        },
    }

    data["audienceIngestion"] = audience_status

    # Update the top-level timestamp only if an actual dataset value changed.
    # This avoids presenting a no-change poll as a new source-data update.
    data["ingestion"] = {
        "source": "BoxOfficeIndia24 + Sacnilk + TMDB",
        "checkedAt": fetched_at,
        "refreshIntervalMinutes": 30,
        "status": "success" if not warnings else "partial",
        "warnings": warnings,
        "sourceStatuses": statuses,
        "note": (
            "A successful fetch means the page was parsed and validated, not that "
            "the publisher estimate is producer-confirmed. Missing values are not inferred."
        ),
    }

    # lastUpdated changes only when source figures or source records change.
    tracked_keys = (
        "dayWiseAdvance", "overseasAllDates", "sacnilkSnapshot", "automatedFeed"
    )
    source_data_changed = any(
        original_data.get(key) != data.get(key) for key in tracked_keys
    )
    if source_data_changed:
        data["lastUpdated"] = fetched_at
    data["lastChecked"] = fetched_at
    atomic_write_json(DATA_FILE, data)

    print(json.dumps(data["ingestion"], ensure_ascii=False, indent=2))
    print(json.dumps(audience_status, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"FATAL: {error}", file=sys.stderr)
        raise SystemExit(1)
