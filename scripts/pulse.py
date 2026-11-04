#!/usr/bin/env python3
"""
ReelMetrics - Audience Pulse

Primary evidence: TMDB written reviews.
Supplementary discovery: YouTube review videos.

VADER analyses actual TMDB review text.
YouTube titles and descriptions are never sentiment evidence.

Only Positive and constructive Mixed themes are published.
Critical-only, abusive and unsupported themes are excluded.

Existing TMDB rating, vote count and review records are preserved.
Audience Pulse updates only its own fields in reviews.json.
"""

import json
import os
import re
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer


# ---------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "reviews.json"

MOVIE_TITLE = "Ramayana: Rise of a Legend"
DEFAULT_MOVIE_ID = 656908

TMDB_API = "https://api.themoviedb.org/3"
YOUTUBE_API = "https://www.googleapis.com/youtube/v3"

MAX_REVIEWS = 60
MAX_REVIEW_PAGES = 5
MIN_REVIEW_LENGTH = 100
MIN_INDEPENDENT_REVIEWS = 2

ANALYZER = SentimentIntensityAnalyzer()

USER_AGENT = "ReelMetrics-AudiencePulse/2.0"


# ---------------------------------------------------------
# TOPIC DEFINITIONS
# ---------------------------------------------------------

TOPICS = {
    "Story & screenplay": [
        "story", "screenplay", "writing", "plot",
        "narrative", "character", "dialogue", "pacing"
    ],

    "Performances": [
        "acting", "performance", "actor", "actress",
        "cast", "portrayal", "chemistry"
    ],

    "Visual experience": [
        "visual", "cinematography", "vfx", "effects",
        "animation", "cgi", "imagery"
    ],

    "Music & sound": [
        "music", "soundtrack", "score",
        "background music", "songs", "sound design"
    ],

    "Action & scale": [
        "action", "battle", "war", "scale",
        "spectacle", "choreography", "stunts"
    ]
}


CONSTRUCTIVE_TERMS = [
    "could", "would", "should", "needs",
    "needed", "could have", "would have",
    "better if", "improve", "improved",
    "improvement", "underdeveloped",
    "too long", "too short", "felt rushed",
    "pacing", "lacked", "less"
]


# ---------------------------------------------------------
# TIME / FILE HELPERS
# ---------------------------------------------------------

def utc_now():
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def load_previous():

    if not OUTPUT.exists():
        return {}

    try:
        data = json.loads(
            OUTPUT.read_text(encoding="utf-8")
        )

        if not isinstance(data, dict):
            raise ValueError("reviews.json root must be an object")

        return data

    except (json.JSONDecodeError, OSError, ValueError) as error:
        raise RuntimeError(
            f"Cannot safely read existing reviews.json: {error}"
        ) from error


def write_output(data):

    OUTPUT.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    fd, temp_name = tempfile.mkstemp(
        prefix=".reviews.",
        suffix=".tmp",
        dir=str(OUTPUT.parent)
    )

    try:

        with os.fdopen(
            fd,
            "w",
            encoding="utf-8"
        ) as handle:

            json.dump(
                data,
                handle,
                ensure_ascii=False,
                indent=2
            )

            handle.write("\n")

        os.replace(
            temp_name,
            OUTPUT
        )

    except Exception:

        try:
            os.unlink(temp_name)
        except OSError:
            pass

        raise


# ---------------------------------------------------------
# HTTP / JSON HELPERS
# ---------------------------------------------------------

def get_json(url, token=None):

    headers = {
        "Accept": "application/json",
        "User-Agent": USER_AGENT
    }

    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = Request(
        url,
        headers=headers
    )

    try:

        with urlopen(request, timeout=30) as response:

            if response.status != 200:
                raise RuntimeError(
                    f"Unexpected HTTP status: {response.status}"
                )

            payload = json.loads(
                response.read().decode("utf-8")
            )

        if not isinstance(payload, dict):
            raise RuntimeError(
                "API returned an unexpected JSON structure"
            )

        return payload

    except HTTPError as error:

        detail = error.read().decode(
            "utf-8",
            errors="replace"
        )[:500]

        raise RuntimeError(
            f"HTTP {error.code}: {detail}"
        ) from error

    except URLError as error:

        raise RuntimeError(
            f"Network error: {error}"
        ) from error


# ---------------------------------------------------------
# MOVIE ID VALIDATION
# ---------------------------------------------------------

def get_movie_id():

    raw_id = os.getenv(
        "TMDB_MOVIE_ID",
        str(DEFAULT_MOVIE_ID)
    ).strip()

    if not raw_id.isdigit() or int(raw_id) <= 0:
        raise RuntimeError(
            "TMDB_MOVIE_ID must be a positive integer"
        )

    return int(raw_id)


def validate_movie(movie, expected_id):

    if not isinstance(movie, dict):
        raise RuntimeError(
            "TMDB returned an invalid movie record"
        )

    if int(movie.get("id") or 0) != expected_id:
        raise RuntimeError(
            "TMDB movie ID validation failed"
        )

    title = str(
        movie.get("title") or ""
    ).strip()

    original_title = str(
        movie.get("original_title") or ""
    ).strip()

    if "ramayana" not in (
        f"{title} {original_title}"
    ).casefold():

        raise RuntimeError(
            "TMDB movie title validation failed"
        )

    return movie


def find_movie(token, movie_id):

    movie = get_json(
        f"{TMDB_API}/movie/{movie_id}?"
        + urlencode({"language": "en-US"}),
        token
    )

    return validate_movie(
        movie,
        movie_id
    )


# ---------------------------------------------------------
# FETCH TMDB WRITTEN REVIEWS
# ---------------------------------------------------------

def fetch_reviews(movie_id, token):

    reviews = []
    seen_ids = set()

    total_results = 0
    total_pages = 0
    pages_fetched = 0

    for page in range(1, MAX_REVIEW_PAGES + 1):

        url = (
            f"{TMDB_API}/movie/{movie_id}/reviews?"
            + urlencode({
                "language": "en-US",
                "page": page
            })
        )

        data = get_json(
            url,
            token
        )

        if page == 1:

            total_results = max(
                0,
                int(data.get("total_results") or 0)
            )

            total_pages = max(
                0,
                int(data.get("total_pages") or 0)
            )

        pages_fetched += 1

        for item in data.get("results", []):

            if not isinstance(item, dict):
                continue

            review_id = str(
                item.get("id") or ""
            )

            if review_id and review_id in seen_ids:
                continue

            content = re.sub(
                r"\s+",
                " ",
                str(item.get("content") or "")
            ).strip()

            if len(content) < MIN_REVIEW_LENGTH:
                continue

            if review_id:
                seen_ids.add(review_id)

            reviews.append({
                "id": review_id or f"review-{len(reviews) + 1}",
                "author": item.get("author") or "Audience member",
                "text": content,
                "url": item.get("url") or "",
                "created_at": item.get("created_at") or ""
            })

            if len(reviews) >= MAX_REVIEWS:
                return reviews, {
                    "totalResults": total_results,
                    "totalPages": total_pages,
                    "pagesFetched": pages_fetched
                }

        if page >= total_pages:
            break

    return reviews, {
        "totalResults": total_results,
        "totalPages": total_pages,
        "pagesFetched": pages_fetched
    }


# ---------------------------------------------------------
# YOUTUBE REVIEW VIDEO DISCOVERY
# ---------------------------------------------------------

def discover_youtube():

    api_key = os.getenv(
        "YOUTUBE_API_KEY",
        ""
    ).strip()

    if not api_key:
        return [], "YouTube API key unavailable."

    queries = [
        f"{MOVIE_TITLE} movie review",
        f"{MOVIE_TITLE} audience review"
    ]

    videos = []
    seen = set()

    for query in queries:

        params = urlencode({
            "part": "snippet",
            "q": query,
            "type": "video",
            "maxResults": 10,
            "order": "date",
            "key": api_key
        })

        data = get_json(
            f"{YOUTUBE_API}/search?{params}"
        )

        for item in data.get("items", []):

            video_id = (
                item.get("id", {}).get("videoId")
            )

            if not video_id or video_id in seen:
                continue

            seen.add(video_id)

            snippet = item.get("snippet", {})

            videos.append({
                "title": snippet.get("title", ""),
                "publishedAt": snippet.get("publishedAt", ""),
                "url": (
                    "https://www.youtube.com/watch?v="
                    + video_id
                )
            })

    return videos, None


# ---------------------------------------------------------
# SENTIMENT ANALYSIS
# ---------------------------------------------------------

def sentence_sentiment(sentence):

    score = ANALYZER.polarity_scores(
        sentence
    )["compound"]

    if score >= 0.05:
        return "Positive", score

    if score <= -0.05:
        return "Negative", score

    return "Neutral", score


def analyse_review(text):

    sentences = re.split(
        r"(?<=[.!?])\s+",
        text
    )

    results = []

    for sentence in sentences:

        sentence = sentence.strip()

        if len(sentence) < 15:
            continue

        sentiment, score = sentence_sentiment(
            sentence
        )

        results.append({
            "text": sentence,
            "sentiment": sentiment,
            "score": score
        })

    return results


# ---------------------------------------------------------
# TOPIC CLASSIFICATION
# ---------------------------------------------------------

def find_topics(review):

    text = review["text"].lower()

    found = []

    for topic, keywords in TOPICS.items():

        if any(
            re.search(
                r"\b" + re.escape(keyword) + r"\b",
                text
            )
            for keyword in keywords
        ):

            found.append(topic)

    return found


def is_constructive(sentence):

    sentence = sentence.lower()

    return any(
        term in sentence
        for term in CONSTRUCTIVE_TERMS
    )


# ---------------------------------------------------------
# BUILD AUDIENCE HIGHLIGHTS
# ---------------------------------------------------------

def build_highlights(reviews):

    topic_data = defaultdict(list)

    for review in reviews:

        topics = find_topics(review)

        if not topics:
            continue

        sentences = analyse_review(
            review["text"]
        )

        for topic in topics:

            topic_data[topic].append({
                "review_id": review["id"],
                "url": review["url"],

                "positive": [
                    item["text"]
                    for item in sentences
                    if item["sentiment"] == "Positive"
                ],

                "negative": [
                    item["text"]
                    for item in sentences
                    if item["sentiment"] == "Negative"
                ]
            })

    highlights = []

    for topic, observations in topic_data.items():

        independent = len({
            item["review_id"]
            for item in observations
        })

        if independent < MIN_INDEPENDENT_REVIEWS:
            continue

        positive_reviews = [
            item for item in observations
            if item["positive"]
        ]

        negative_reviews = [
            item for item in observations
            if item["negative"]
        ]

        if (
            len(positive_reviews) >= MIN_INDEPENDENT_REVIEWS
            and len(positive_reviews) > len(negative_reviews)
        ):

            sentiment = "Positive"

            summary = (
                f"Feedback on {topic.lower()} leans positive "
                f"in this collected sample. "
                f"{len(positive_reviews)} independent reviews "
                "contain positive observations. "
                "This is a sample-level signal, not a claim "
                "about every viewer."
            )

        elif positive_reviews and negative_reviews:

            constructive = [
                sentence
                for item in observations
                for sentence in item["negative"]
                if is_constructive(sentence)
            ]

            if not constructive:
                continue

            sentiment = "Mixed"

            summary = (
                f"Feedback on {topic.lower()} is mixed. "
                f"{len(positive_reviews)} reviews contain "
                f"positive observations, while "
                f"{len(negative_reviews)} include criticism. "
                "Some criticism identifies specific areas "
                "for improvement."
            )

        else:
            continue

        evidence = []

        for item in observations:

            if item["positive"] or item["negative"]:

                evidence.append({
                    "source": "TMDB",
                    "review_id": item["review_id"],
                    "url": item["url"]
                })

        highlights.append({
            "category": topic,
            "sentiment": sentiment,
            "text": summary,
            "evidence": evidence
        })

    highlights.sort(
        key=lambda item: (
            len(item["evidence"]),
            item["sentiment"] == "Positive"
        ),
        reverse=True
    )

    return highlights[:4]


# ---------------------------------------------------------
# MERGE AUDIENCE PULSE INTO EXISTING JSON
# ---------------------------------------------------------

def merge_pulse(
    previous,
    highlights,
    reviews,
    review_stats,
    youtube_videos,
    youtube_error,
    movie_id,
    status,
    message,
    now
):

    output = dict(previous)

    # Preserve TMDB rating, vote count, review records,
    # movie metadata and any unrelated existing fields.
    output["movie"] = MOVIE_TITLE
    output["status"] = status
    output["updatedAt"] = now
    output["highlights"] = highlights
    output["message"] = message

    output["audiencePulse"] = {
        "status": status,
        "updatedAt": now,
        "sentimentEngine": "VADER",
        "reviewsAnalysed": len(reviews),
        "minimumReviewLength": MIN_REVIEW_LENGTH,
        "minimumIndependentReviews": MIN_INDEPENDENT_REVIEWS
    }

    output["internalAudit"] = {
        **(
            previous.get("internalAudit", {})
            if isinstance(previous.get("internalAudit"), dict)
            else {}
        ),

        "tmdbMovieId": movie_id,
        "tmdbReviewCount": review_stats["totalResults"],
        "tmdbReviewRecordsFetched": len(reviews),
        "tmdbReviewPagesFetched": review_stats["pagesFetched"],
        "tmdbReviewPagesAvailable": review_stats["totalPages"],

        "youtubeVideosDiscovered": youtube_videos,
        "youtubeUsedAsSentimentEvidence": False,
        "youtubeDiscoveryError": youtube_error,

        "sentimentEngine": "VADER",
        "publishedHighlightCount": len(highlights),
        "sentimentAnalysis": "completed"
    }

    return output


# ---------------------------------------------------------
# MAIN PROCESS
# ---------------------------------------------------------

def main():

    previous = load_previous()
    now = utc_now()

    try:

        token = os.getenv(
            "TMDB_READ_ACCESS_TOKEN",
            ""
        ).strip()

        if not token:
            raise RuntimeError(
                "Missing TMDB_READ_ACCESS_TOKEN GitHub secret."
            )

        movie_id = get_movie_id()

        movie = find_movie(
            token,
            movie_id
        )

        # YouTube is supplementary discovery only.
        youtube_videos = []
        youtube_error = None

        try:

            youtube_videos, youtube_error = (
                discover_youtube()
            )

        except Exception as error:

            youtube_error = str(error)

            print(
                f"YouTube discovery unavailable: {error}",
                file=sys.stderr
            )

        print(
            f"YouTube videos discovered: {len(youtube_videos)}"
        )

        # Fetch actual written TMDB reviews.
        reviews, review_stats = fetch_reviews(
            movie_id,
            token
        )

        print(
            f"TMDB total reviews: {review_stats['totalResults']}"
        )

        print(
            f"Substantial reviews fetched: {len(reviews)}"
        )

        # Insufficient evidence: retain existing highlights.
        if len(reviews) < MIN_INDEPENDENT_REVIEWS:

            output = merge_pulse(
                previous=previous,
                highlights=previous.get("highlights", []),
                reviews=reviews,
                review_stats=review_stats,
                youtube_videos=youtube_videos,
                youtube_error=youtube_error,
                movie_id=movie_id,
                status="awaiting_reviews",
                message=(
                    f"Only {len(reviews)} substantial TMDB reviews "
                    "found. Waiting for genuine audience reviews."
                ),
                now=now
            )

            write_output(output)

            print(output["message"])
            return

        highlights = build_highlights(
            reviews
        )

        # No eligible sentiment themes.
        if not highlights:

            output = merge_pulse(
                previous=previous,
                highlights=previous.get("highlights", []),
                reviews=reviews,
                review_stats=review_stats,
                youtube_videos=youtube_videos,
                youtube_error=youtube_error,
                movie_id=movie_id,
                status="awaiting_evidence",
                message=(
                    "No Positive or constructive Mixed themes "
                    "met the evidence rules. Previous highlights "
                    "have been retained."
                ),
                now=now
            )

            write_output(output)

            print(output["message"])
            return

        # Successful update.
        output = merge_pulse(
            previous=previous,
            highlights=highlights,
            reviews=reviews,
            review_stats=review_stats,
            youtube_videos=youtube_videos,
            youtube_error=youtube_error,
            movie_id=movie_id,
            status="success",
            message=(
                "Audience Pulse generated from actual TMDB "
                "written reviews. YouTube discovery is not "
                "used as sentiment evidence."
            ),
            now=now
        )

        write_output(output)

        print(
            f"Published {len(highlights)} highlights "
            f"from {len(reviews)} TMDB reviews."
        )

    except Exception as error:

        # Never destroy the last known-good audience data.
        output = dict(previous)

        output["movie"] = MOVIE_TITLE

        output["status"] = (
            "stale"
            if previous.get("highlights")
            else "error"
        )

        output["lastAttemptAt"] = now
        output["lastError"] = str(error)

        output.setdefault(
            "highlights",
            []
        )

        # Atomic write preserves the previous ratings and reviews.
        write_output(output)

        print(
            f"Audience Pulse not replaced: {error}",
            file=sys.stderr
        )

        raise


# ---------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------

if __name__ == "__main__":

    try:
        main()

    except Exception as error:

        print(
            f"FATAL: {error}",
            file=sys.stderr
        )

        raise SystemExit(1)
