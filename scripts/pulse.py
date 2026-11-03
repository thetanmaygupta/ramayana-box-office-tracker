
#!/usr/bin/env python3
"""
ReelMetrics — Audience Pulse

Primary source: TMDB written reviews.
Supplementary source: YouTube review-video discovery.

VADER analyses TMDB review text only.
YouTube titles and descriptions are never treated as sentiment evidence.

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


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "reviews.json"

MOVIE_TITLE = "Ramayana: Rise of a Legend"
TMDB_API = "https://api.themoviedb.org/3"
YOUTUBE_API = "https://www.googleapis.com/youtube/v3"

ANALYZER = SentimentIntensityAnalyzer()

MAX_REVIEWS = 60
MIN_REVIEW_LENGTH = 100
MIN_INDEPENDENT_REVIEWS = 2

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
        "music", "soundtrack", "score", "background music",
        "songs", "sound design", "audio"
    ],
    "Action & scale": [
        "action", "battle", "war", "scale",
        "spectacle", "choreography", "stunts"
    ],
}

CONSTRUCTIVE_TERMS = (
    "could", "would", "should", "needs", "need more",
    "needed", "could have", "would have", "better if",
    "improve", "improved", "improvement",
    "more development", "underdeveloped", "too long",
    "too short", "felt rushed", "pacing", "lacked", "less"
)


def get_json(url, token=None):
    headers = {"Accept": "application/json"}

    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = Request(url, headers=headers)

    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(
            f"HTTP {error.code}: {detail}"
        ) from error

    except URLError as error:
        raise RuntimeError(f"Network error: {error}") from error


def find_movie(token):
    params = urlencode({
        "query": MOVIE_TITLE,
        "include_adult": "false",
        "language": "en-US"
    })

    data = get_json(
        f"{TMDB_API}/search/movie?{params}",
        token
    )

    normalize = lambda value: re.sub(
        r"[^a-z0-9]", "", value.lower()
    )

    expected = normalize(MOVIE_TITLE)

    matches = [
        movie for movie in data.get("results", [])
        if normalize(movie.get("title") or "") == expected
    ]

    if not matches:
        raise RuntimeError(
            "Exact TMDB title not found. Verify TMDB_MOVIE_ID."
        )

    matches.sort(
        key=lambda movie: (
            movie.get("release_date") or "",
            movie.get("popularity") or 0
        ),
        reverse=True
    )

    return matches[0]


def fetch_reviews(movie_id, token):
    reviews = []

    for page in range(1, 4):
        params = urlencode({
            "language": "en-US",
            "page": page
        })

        data = get_json(
            f"{TMDB_API}/movie/{movie_id}/reviews?{params}",
            token
        )

        for item in data.get("results", []):
            content = re.sub(
                r"\s+", " ", item.get("content") or ""
            ).strip()

            if len(content) < MIN_REVIEW_LENGTH:
                continue

            reviews.append({
                "id": len(reviews) + 1,
                "text": content[:6000],
                "url": item.get("url") or "",
                "author": item.get("author") or "TMDB reviewer",
                "created_at": item.get("created_at") or ""
            })

            if len(reviews) >= MAX_REVIEWS:
                return reviews

        if page >= int(data.get("total_pages") or 1):
            break

    return reviews


def discover_youtube():
    """Find relevant review videos; do not infer their spoken content."""

    key = os.getenv("YOUTUBE_API_KEY", "").strip()

    if not key:
        return [], "YOUTUBE_API_KEY is missing."

    videos = {}

    queries = [
        f'"{MOVIE_TITLE}" movie review',
        f'"{MOVIE_TITLE}" audience review'
    ]

    try:
        for query in queries:
            params = urlencode({
                "part": "snippet",
                "q": query,
                "type": "video",
                "maxResults": 5,
                "order": "date",
                "key": key
            })

            data = get_json(
                f"{YOUTUBE_API}/search?{params}"
            )

            for item in data.get("items", []):
                video_id = (
                    item.get("id") or {}
                ).get("videoId")

                if not video_id:
                    continue

                snippet = item.get("snippet") or {}

                videos[video_id] = {
                    "title": (snippet.get("title") or "")[:180],
                    "channelTitle": (
                        snippet.get("channelTitle") or ""
                    )[:120],
                    "publishedAt": (
                        snippet.get("publishedAt") or ""
                    ),
                    "url": (
                        f"https://www.youtube.com/watch?v={video_id}"
                    )
                }

        return list(videos.values()), None

    except Exception as error:
        # YouTube failure must not stop TMDB processing.
        return list(videos.values()), str(error)


def split_sentences(text):
    return [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", text)
        if len(sentence.strip()) >= 12
    ]


def topic_sentences(review_text, keywords):
    matches = []

    for sentence in split_sentences(review_text):
        lower = sentence.lower()

        if any(
            re.search(
                r"\b" + re.escape(term) + r"\b",
                lower
            )
            for term in keywords
        ):
            score = ANALYZER.polarity_scores(
                sentence
            )["compound"]

            matches.append((sentence, score))

    return matches


def analyse_topics(reviews):
    by_topic = defaultdict(list)

    for review in reviews:
        for topic, keywords in TOPICS.items():
            sentences = topic_sentences(
                review["text"], keywords
            )

            if not sentences:
                continue

            positive = [
                sentence for sentence, score in sentences
                if score >= 0.05
            ]

            critical = [
                sentence for sentence, score in sentences
                if score <= -0.05
            ]

            neutral = [
                sentence for sentence, score in sentences
                if -0.05 < score < 0.05
            ]

            by_topic[topic].append({
                "review_id": review["id"],
                "url": review["url"],
                "positive": positive,
                "critical": critical,
                "neutral": neutral
            })

    return by_topic


def build_highlights(reviews):
    topic_data = analyse_topics(reviews)
    highlights = []

    for topic, observations in topic_data.items():

        independent = len({
            item["review_id"] for item in observations
        })

        if independent < MIN_INDEPENDENT_REVIEWS:
            continue

        positive_reviews = [
            item for item in observations
            if item["positive"]
        ]

        critical_reviews = [
            item for item in observations
            if item["critical"]
        ]

        if (
            len(positive_reviews) >= MIN_INDEPENDENT_REVIEWS
            and len(positive_reviews) > len(critical_reviews)
        ):
            sentiment = "Positive"

            summary = (
                f"Topic-specific comments on {topic.lower()} "
                f"lean positive in this sample: "
                f"{len(positive_reviews)} of "
                f"{len(observations)} reviews contain positive "
                "observations. This describes the collected "
                "TMDB sample, not all viewers."
            )

        elif positive_reviews and critical_reviews:

            constructive = [
                sentence
                for item in observations
                for sentence in item["critical"]
                if any(
                    term in sentence.lower()
                    for term in CONSTRUCTIVE_TERMS
                )
            ]

            if not constructive:
                continue

            sentiment = "Mixed"

            summary = (
                f"Feedback on {topic.lower()} is mixed in this "
                f"sample: {len(positive_reviews)} reviews contain "
                f"positive observations, while "
                f"{len(critical_reviews)} include criticism. "
                "Some criticism includes specific improvement "
                "or execution suggestions."
            )

        else:
            # Critical-only and neutral-only themes are excluded.
            continue

        evidence = []

        for item in observations:
            if item["positive"] or item["critical"]:
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


def write_output(data):
    OUTPUT.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8"
    )


def main():
    previous = {}

    if OUTPUT.exists():
        try:
            previous = json.loads(
                OUTPUT.read_text(encoding="utf-8")
            )
        except (json.JSONDecodeError, OSError):
            previous = {}

    now = datetime.now(timezone.utc).isoformat(
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

        # Use the verified repository variable when available.
        movie_id = os.getenv("TMDB_MOVIE_ID", "").strip()

        if movie_id:
            movie = {
                "id": int(movie_id),
                "title": MOVIE_TITLE
            }
        else:
            movie = find_movie(token)

        # YouTube discovery runs independently of TMDB reviews.
        youtube_videos, youtube_error = discover_youtube()

        print(
            f"YouTube discovery: {len(youtube_videos)} videos."
        )

        # Fetch actual written reviews from TMDB.
        reviews = fetch_reviews(movie["id"], token)

        print(
            f"TMDB substantial written reviews: {len(reviews)}."
        )

        audit = {
            "tmdbMovieId": movie["id"],
            "tmdbReviewCount": len(reviews),
            "youtubeVideosDiscovered": youtube_videos,
            "youtubeUsedAsSentimentEvidence": False,
            "youtubeDiscoveryError": youtube_error,
            "sentimentEngine": "VADER"
        }

        # Insufficient reviews: preserve previous highlights.
        if len(reviews) < MIN_INDEPENDENT_REVIEWS:
            output = {
                "movie": MOVIE_TITLE,
                "status": "awaiting_reviews",
                "updatedAt": now,
                "highlights": previous.get("highlights", []),
                "message": (
                    f"Only {len(reviews)} substantial TMDB reviews "
                    "found. Waiting for genuine audience reviews."
                ),
                "internalAudit": audit
            }

            write_output(output)
            print(output["message"])
            return

        # Analyse TMDB review text using VADER.
        highlights = build_highlights(reviews)

        # No eligible evidence: do not fabricate or erase previous content.
        if not highlights:
            output = {
                "movie": MOVIE_TITLE,
                "status": "awaiting_evidence",
                "updatedAt": now,
                "highlights": previous.get("highlights", []),
                "message": (
                    "No Positive or constructive Mixed themes "
                    "met the evidence rules."
                ),
                "internalAudit": audit
            }

            write_output(output)
            print(output["message"])
            return

        output = {
            "movie": MOVIE_TITLE,
            "status": "success",
            "updatedAt": now,
            "highlights": highlights,
            "editorialNote": (
                "Original rule-based summaries of TMDB written "
                "reviews. Only Positive and constructive Mixed "
                "themes are published."
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

#!/usr/bin/env python3
"""
ReelMetrics — Audience Pulse

Primary source: TMDB written reviews.
Supplementary source: YouTube review-video discovery.

VADER analyses TMDB review text only.
YouTube titles and descriptions are never treated as sentiment evidence.

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


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "reviews.json"

MOVIE_TITLE = "Ramayana: Rise of a Legend"
TMDB_API = "https://api.themoviedb.org/3"
YOUTUBE_API = "https://www.googleapis.com/youtube/v3"

ANALYZER = SentimentIntensityAnalyzer()

MAX_REVIEWS = 60
MIN_REVIEW_LENGTH = 100
MIN_INDEPENDENT_REVIEWS = 2

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
        "music", "soundtrack", "score", "background music",
        "songs", "sound design", "audio"
    ],
    "Action & scale": [
        "action", "battle", "war", "scale",
        "spectacle", "choreography", "stunts"
    ],
}

CONSTRUCTIVE_TERMS = (
    "could", "would", "should", "needs", "need more",
    "needed", "could have", "would have", "better if",
    "improve", "improved", "improvement",
    "more development", "underdeveloped", "too long",
    "too short", "felt rushed", "pacing", "lacked", "less"
)


def get_json(url, token=None):
    headers = {"Accept": "application/json"}

    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = Request(url, headers=headers)

    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(
            f"HTTP {error.code}: {detail}"
        ) from error

    except URLError as error:
        raise RuntimeError(f"Network error: {error}") from error


def find_movie(token):
    params = urlencode({
        "query": MOVIE_TITLE,
        "include_adult": "false",
        "language": "en-US"
    })

    data = get_json(
        f"{TMDB_API}/search/movie?{params}",
        token
    )

    normalize = lambda value: re.sub(
        r"[^a-z0-9]", "", value.lower()
    )

    expected = normalize(MOVIE_TITLE)

    matches = [
        movie for movie in data.get("results", [])
        if normalize(movie.get("title") or "") == expected
    ]

    if not matches:
        raise RuntimeError(
            "Exact TMDB title not found. Verify TMDB_MOVIE_ID."
        )

    matches.sort(
        key=lambda movie: (
            movie.get("release_date") or "",
            movie.get("popularity") or 0
        ),
        reverse=True
    )

    return matches[0]


def fetch_reviews(movie_id, token):
    reviews = []

    for page in range(1, 4):
        params = urlencode({
            "language": "en-US",
            "page": page
        })

        data = get_json(
            f"{TMDB_API}/movie/{movie_id}/reviews?{params}",
            token
        )

        for item in data.get("results", []):
            content = re.sub(
                r"\s+", " ", item.get("content") or ""
            ).strip()

            if len(content) < MIN_REVIEW_LENGTH:
                continue

            reviews.append({
                "id": len(reviews) + 1,
                "text": content[:6000],
                "url": item.get("url") or "",
                "author": item.get("author") or "TMDB reviewer",
                "created_at": item.get("created_at") or ""
            })

            if len(reviews) >= MAX_REVIEWS:
                return reviews

        if page >= int(data.get("total_pages") or 1):
            break

    return reviews


def discover_youtube():
    """Find relevant review videos; do not infer their spoken content."""

    key = os.getenv("YOUTUBE_API_KEY", "").strip()

    if not key:
        return [], "YOUTUBE_API_KEY is missing."

    videos = {}

    queries = [
        f'"{MOVIE_TITLE}" movie review',
        f'"{MOVIE_TITLE}" audience review'
    ]

    try:
        for query in queries:
            params = urlencode({
                "part": "snippet",
                "q": query,
                "type": "video",
                "maxResults": 5,
                "order": "date",
                "key": key
            })

            data = get_json(
                f"{YOUTUBE_API}/search?{params}"
            )

            for item in data.get("items", []):
                video_id = (
                    item.get("id") or {}
                ).get("videoId")

                if not video_id:
                    continue

                snippet = item.get("snippet") or {}

                videos[video_id] = {
                    "title": (snippet.get("title") or "")[:180],
                    "channelTitle": (
                        snippet.get("channelTitle") or ""
                    )[:120],
                    "publishedAt": (
                        snippet.get("publishedAt") or ""
                    ),
                    "url": (
                        f"https://www.youtube.com/watch?v={video_id}"
                    )
                }

        return list(videos.values()), None

    except Exception as error:
        # YouTube failure must not stop TMDB processing.
        return list(videos.values()), str(error)


def split_sentences(text):
    return [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", text)
        if len(sentence.strip()) >= 12
    ]


def topic_sentences(review_text, keywords):
    matches = []

    for sentence in split_sentences(review_text):
        lower = sentence.lower()

        if any(
            re.search(
                r"\b" + re.escape(term) + r"\b",
                lower
            )
            for term in keywords
        ):
            score = ANALYZER.polarity_scores(
                sentence
            )["compound"]

            matches.append((sentence, score))

    return matches


def analyse_topics(reviews):
    by_topic = defaultdict(list)

    for review in reviews:
        for topic, keywords in TOPICS.items():
            sentences = topic_sentences(
                review["text"], keywords
            )

            if not sentences:
                continue

            positive = [
                sentence for sentence, score in sentences
                if score >= 0.05
            ]

            critical = [
                sentence for sentence, score in sentences
                if score <= -0.05
            ]

            neutral = [
                sentence for sentence, score in sentences
                if -0.05 < score < 0.05
            ]

            by_topic[topic].append({
                "review_id": review["id"],
                "url": review["url"],
                "positive": positive,
                "critical": critical,
                "neutral": neutral
            })

    return by_topic


def build_highlights(reviews):
    topic_data = analyse_topics(reviews)
    highlights = []

    for topic, observations in topic_data.items():

        independent = len({
            item["review_id"] for item in observations
        })

        if independent < MIN_INDEPENDENT_REVIEWS:
            continue

        positive_reviews = [
            item for item in observations
            if item["positive"]
        ]

        critical_reviews = [
            item for item in observations
            if item["critical"]
        ]

        if (
            len(positive_reviews) >= MIN_INDEPENDENT_REVIEWS
            and len(positive_reviews) > len(critical_reviews)
        ):
            sentiment = "Positive"

            summary = (
                f"Topic-specific comments on {topic.lower()} "
                f"lean positive in this sample: "
                f"{len(positive_reviews)} of "
                f"{len(observations)} reviews contain positive "
                "observations. This describes the collected "
                "TMDB sample, not all viewers."
            )

        elif positive_reviews and critical_reviews:

            constructive = [
                sentence
                for item in observations
                for sentence in item["critical"]
                if any(
                    term in sentence.lower()
                    for term in CONSTRUCTIVE_TERMS
                )
            ]

            if not constructive:
                continue

            sentiment = "Mixed"

            summary = (
                f"Feedback on {topic.lower()} is mixed in this "
                f"sample: {len(positive_reviews)} reviews contain "
                f"positive observations, while "
                f"{len(critical_reviews)} include criticism. "
                "Some criticism includes specific improvement "
                "or execution suggestions."
            )

        else:
            # Critical-only and neutral-only themes are excluded.
            continue

        evidence = []

        for item in observations:
            if item["positive"] or item["critical"]:
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


def write_output(data):
    OUTPUT.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8"
    )


def main():
    previous = {}

    if OUTPUT.exists():
        try:
            previous = json.loads(
                OUTPUT.read_text(encoding="utf-8")
            )
        except (json.JSONDecodeError, OSError):
            previous = {}

    now = datetime.now(timezone.utc).isoformat(
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

        # Use the verified repository variable when available.
        movie_id = os.getenv("TMDB_MOVIE_ID", "").strip()

        if movie_id:
            movie = {
                "id": int(movie_id),
                "title": MOVIE_TITLE
            }
        else:
            movie = find_movie(token)

        # YouTube discovery runs independently of TMDB reviews.
        youtube_videos, youtube_error = discover_youtube()

        print(
            f"YouTube discovery: {len(youtube_videos)} videos."
        )

        # Fetch actual written reviews from TMDB.
        reviews = fetch_reviews(movie["id"], token)

        print(
            f"TMDB substantial written reviews: {len(reviews)}."
        )

        audit = {
            "tmdbMovieId": movie["id"],
            "tmdbReviewCount": len(reviews),
            "youtubeVideosDiscovered": youtube_videos,
            "youtubeUsedAsSentimentEvidence": False,
            "youtubeDiscoveryError": youtube_error,
            "sentimentEngine": "VADER"
        }

        # Insufficient reviews: preserve previous highlights.
        if len(reviews) < MIN_INDEPENDENT_REVIEWS:
            output = {
                "movie": MOVIE_TITLE,
                "status": "awaiting_reviews",
                "updatedAt": now,
                "highlights": previous.get("highlights", []),
                "message": (
                    f"Only {len(reviews)} substantial TMDB reviews "
                    "found. Waiting for genuine audience reviews."
                ),
                "internalAudit": audit
            }

            write_output(output)
            print(output["message"])
            return

        # Analyse TMDB review text using VADER.
        highlights = build_highlights(reviews)

        # No eligible evidence: do not fabricate or erase previous content.
        if not highlights:
            output = {
                "movie": MOVIE_TITLE,
                "status": "awaiting_evidence",
                "updatedAt": now,
                "highlights": previous.get("highlights", []),
                "message": (
                    "No Positive or constructive Mixed themes "
                    "met the evidence rules."
                ),
                "internalAudit": audit
            }

            write_output(output)
            print(output["message"])
            return

        output = {
            "movie": MOVIE_TITLE,
            "status": "success",
            "updatedAt": now,
            "highlights": highlights,
            "editorialNote": (
                "Original rule-based summaries of TMDB written "
                "reviews. Only Positive and constructive Mixed "
                "themes are published."
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
            else "insufficient_data"
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


if __name__ == "__main__":
    main()
    
    except Exception as error:
        previous["movie"] = MOVIE_TITLE

        previous["status"] = (
            "stale"
            if previous.get("highlights")
            else "insufficient_data"
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


if __name__ == "__main__":
    main()
