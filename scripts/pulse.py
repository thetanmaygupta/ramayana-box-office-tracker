
#!/usr/bin/env python3
"""
ReelMetrics - Audience Pulse

Primary source: TMDB written reviews.
Supplementary source: YouTube review-video discovery.

VADER analyses actual TMDB review text.
YouTube titles and descriptions are NOT treated as review transcripts.

Only Positive and constructive Mixed themes are published.
Critical-only, abusive and unsupported themes are excluded.

If evidence is insufficient, previous highlights are retained.
No audience opinions are fabricated.
"""

import json
import os
import re
import sys
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

TMDB_API = "https://api.themoviedb.org/3"
YOUTUBE_API = "https://www.googleapis.com/youtube/v3"

MAX_REVIEWS = 60
MIN_REVIEW_LENGTH = 100
MIN_INDEPENDENT_REVIEWS = 2

ANALYZER = SentimentIntensityAnalyzer()


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
# HTTP / JSON HELPERS
# ---------------------------------------------------------

def get_json(url, token=None):

    headers = {
        "Accept": "application/json",
        "User-Agent": "ReelMetrics-AudiencePulse/1.0"
    }

    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = Request(url, headers=headers)

    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(
                response.read().decode("utf-8")
            )

    except HTTPError as error:
        detail = error.read().decode(
            "utf-8", errors="replace"
        )[:500]

        raise RuntimeError(
            f"HTTP {error.code}: {detail}"
        )

    except URLError as error:
        raise RuntimeError(
            f"Network error: {error}"
        )


# ---------------------------------------------------------
# TMDB MOVIE LOOKUP
# ---------------------------------------------------------

def find_movie(token):

    query = urlencode({
        "query": MOVIE_TITLE,
        "include_adult": "false",
        "language": "en-US"
    })

    data = get_json(
        f"{TMDB_API}/search/movie?{query}",
        token
    )

    results = data.get("results", [])

    expected = re.sub(
        r"[^a-z0-9]",
        "",
        MOVIE_TITLE.lower()
    )

    matches = [
        movie for movie in results
        if re.sub(
            r"[^a-z0-9]",
            "",
            (movie.get("title") or "").lower()
        ) == expected
    ]

    if not matches:
        raise RuntimeError(
            "Exact movie title not found on TMDB. "
            "Check the TMDB_MOVIE_ID repository variable."
        )

    return matches[0]


# ---------------------------------------------------------
# FETCH TMDB WRITTEN REVIEWS
# ---------------------------------------------------------

def fetch_reviews(movie_id, token):

    reviews = []

    for page in range(1, 6):

        url = (
            f"{TMDB_API}/movie/{movie_id}/reviews?"
            + urlencode({
                "language": "en-US",
                "page": page
            })
        )

        data = get_json(url, token)

        for item in data.get("results", []):

            content = re.sub(
                r"\s+",
                " ",
                item.get("content") or ""
            ).strip()

            if len(content) < MIN_REVIEW_LENGTH:
                continue

            reviews.append({
                "id": len(reviews) + 1,
                "author": item.get("author", "Audience member"),
                "text": content,
                "url": item.get("url", ""),
                "created_at": item.get("created_at", "")
            })

            if len(reviews) >= MAX_REVIEWS:
                return reviews

        if page >= int(data.get("total_pages") or 1):
            break

    return reviews


# ---------------------------------------------------------
# YOUTUBE REVIEW VIDEO DISCOVERY
# ---------------------------------------------------------

def discover_youtube():

    api_key = os.getenv(
        "YOUTUBE_API_KEY", ""
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

        # Positive theme
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

        # Mixed but constructive theme
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
            # Critical-only and neutral-only themes excluded.
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
# WRITE JSON
# ---------------------------------------------------------

def write_output(data):

    OUTPUT.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2
        ) + "\n",
        encoding="utf-8"
    )


# ---------------------------------------------------------
# MAIN PROCESS
# ---------------------------------------------------------

def main():

    previous = {}

    if OUTPUT.exists():

        try:
            previous = json.loads(
                OUTPUT.read_text(
                    encoding="utf-8"
                )
            )

        except (json.JSONDecodeError, OSError):
            previous = {}

    now = datetime.now(
        timezone.utc
    ).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")

    try:

        token = os.getenv(
            "TMDB_READ_ACCESS_TOKEN", ""
        ).strip()

        if not token:
            raise RuntimeError(
                "Missing TMDB_READ_ACCESS_TOKEN GitHub secret."
            )

        movie_id = os.getenv(
            "TMDB_MOVIE_ID", ""
        ).strip()

        if movie_id:

            movie = {
                "id": int(movie_id),
                "title": MOVIE_TITLE
            }

        else:
            movie = find_movie(token)

        # Discover YouTube review videos.
        youtube_videos, youtube_error = (
            discover_youtube()
        )

        print(
            f"YouTube videos discovered: "
            f"{len(youtube_videos)}"
        )

        # Fetch actual written reviews.
        reviews = fetch_reviews(
            movie["id"],
            token
        )

        print(
            f"TMDB written reviews found: "
            f"{len(reviews)}"
        )

        audit = {
            "tmdbMovieId": movie["id"],
            "tmdbReviewCount": len(reviews),
            "youtubeVideosDiscovered": youtube_videos,
            "youtubeUsedAsSentimentEvidence": False,
            "youtubeDiscoveryError": youtube_error,
            "sentimentEngine": "VADER"
        }

        # No sufficient evidence.
        if len(reviews) < MIN_INDEPENDENT_REVIEWS:

            output = {
                "movie": MOVIE_TITLE,
                "status": "awaiting_reviews",
                "updatedAt": now,
                "highlights": previous.get(
                    "highlights", []
                ),
                "message": (
                    f"Only {len(reviews)} substantial "
                    "TMDB reviews found. Waiting for "
                    "genuine audience reviews."
                ),
                "internalAudit": audit
            }

            write_output(output)

            print(output["message"])
            return

        highlights = build_highlights(reviews)

        # No eligible sentiment themes.
        if not highlights:

            output = {
                "movie": MOVIE_TITLE,
                "status": "awaiting_evidence",
                "updatedAt": now,
                "highlights": previous.get(
                    "highlights", []
                ),
                "message": (
                    "No Positive or constructive Mixed "
                    "themes met the evidence rules."
                ),
                "internalAudit": audit
            }

            write_output(output)

            print(output["message"])
            return

        # Successful update.
        output = {
            "movie": MOVIE_TITLE,
            "status": "success",
            "updatedAt": now,
            "highlights": highlights,
            "editorialNote": (
                "Original rule-based summaries of "
                "TMDB written reviews. Only Positive "
                "and constructive Mixed themes "
                "are published."
            ),
            "internalAudit": {
                **audit,
                "publishedHighlightCount": len(highlights)
            }
        }

        write_output(output)

        print(
            f"Published {len(highlights)} highlights "
            f"from {len(reviews)} TMDB reviews."
        )

    except Exception as error:

        previous["movie"] = MOVIE_TITLE

        previous["status"] = (
            "stale"
            if previous.get("highlights")
            else "error"
        )

        previous["lastAttemptAt"] = now
        previous["lastError"] = str(error)

        previous.setdefault("highlights", [])

        write_output(previous)

        print(
            f"Audience Pulse not replaced: {error}",
            file=sys.stderr
        )

        raise


# ---------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------

if __name__ == "__main__":
    main()
