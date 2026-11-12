"""EdgeRank feed ranking over social_network.db (Sovereign Social Network).

Formula
-------
    Score(u, p) = Affinity(u, p) * Weight(type(p)) * Decay(age(p))

Component definitions (all knobs are module-level constants):

Affinity(u, p) -- how strongly viewer u is connected to post p. Sum of two
    interaction signals:
        REL_WEIGHT  * relationship(u, author(p))
      + PROOF_WEIGHT * log1p(likes_count(p))
    `relationship` is the stored friendship affinity_score when u and the
    post's author are friends (either direction in `friendships`), otherwise
    DISCOVERY_FLOOR, so non-friend / cold-start content still surfaces.
    Social proof uses log1p so a viral post cannot outscore relationship
    signal alone -- relationships first, reach second (the growth stance).

Weight(type) -- per-content-type multiplier from POST_TYPE_WEIGHTS. The DB has
    no post_type column, so the type is classified deterministically from the
    post content (see classify_post_type): media extensions/keywords win over
    a bare link; anything else ranks as text.

Decay(age)   -- recency factor 1 / (1 + DECAY_LAMBDA_PER_HOUR * hours), which
    halves roughly every 10 hours. Age is clamped at zero so future-dated
    rows can never outscore fresh ones.

rank_feed(viewer_id) returns one dict per post, sorted by exact score in
strictly descending order (ties broken newest-first, then id, for
determinism). Each row carries the full EdgeRank breakdown: `post_type`,
`affinity`, `weight`, `decay`, `edgerank_score`.
"""
from __future__ import annotations

import math
import re
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


# --------------------------------------------------------------------------- #
# Tunables
# --------------------------------------------------------------------------- #
POST_TYPE_WEIGHTS: Dict[str, float] = {
    "text": 1.0,
    "link": 2.0,
    "photo": 3.0,
    "video": 4.0,
}

DISCOVERY_FLOOR = 0.2          # relationship floor for non-friend authors
REL_WEIGHT = 1.5               # scaling on stored friendship affinity_score
PROOF_WEIGHT = 0.4             # scaling on log1p(likes_count) social proof
DECAY_LAMBDA_PER_HOUR = 0.1    # decay halves roughly every 10 hours

_IMAGE_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp")
_VIDEO_EXT = (".mp4", ".mov", ".webm")
_URL_RE = re.compile(r"https?://\S+")


# --------------------------------------------------------------------------- #
# Post-type classification (no post_type column exists in the schema)
# --------------------------------------------------------------------------- #
def classify_post_type(content: Optional[str]) -> str:
    """Deterministically map post content to a weighted content type.

    Priority: media URL extension > media keyword > bare link > text.
    """
    text = (content or "").strip()
    lowered = text.lower()

    urls = _URL_RE.findall(text)
    for url in urls:
        path = url.split("?", 1)[0].rstrip("/").lower()
        if path.endswith(_IMAGE_EXT):
            return "photo"
        if path.endswith(_VIDEO_EXT):
            return "video"

    if "video" in lowered or any(ext in lowered for ext in _VIDEO_EXT):
        return "video"
    if "photo" in lowered or "image" in lowered:
        return "photo"
    if urls:
        return "link"
    return "text"


# --------------------------------------------------------------------------- #
# Parsing helpers
# --------------------------------------------------------------------------- #
def _parse_created_at(value: Optional[str]) -> float:
    """Epoch seconds (UTC) for a SQLite TIMESTAMP; unparseable => now."""
    if not value:
        return time.time()
    raw = str(value).strip()
    dt: Optional[datetime] = None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(raw, fmt)
            break
        except ValueError:
            continue
    if dt is None:
        return time.time()
    if dt.tzinfo is None:
        # SQLite CURRENT_TIMESTAMP is UTC; the stored value has no zone marker.
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _load_friendship_affinities(cur: sqlite3.Cursor) -> Dict[int, Dict[int, float]]:
    """Bidirectional map viewer_id -> {friend_id: affinity_score}."""
    graph: Dict[int, Dict[int, float]] = {}
    cur.execute("SELECT user_id_a, user_id_b, affinity_score FROM friendships")
    for a, b, score in cur.fetchall():
        graph.setdefault(a, {})[b] = score if score is not None else 0.5
        graph.setdefault(b, {})[a] = score if score is not None else 0.5
    return graph


# --------------------------------------------------------------------------- #
# Ranker
# --------------------------------------------------------------------------- #
class FeedRanker:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def populate_synthetic_posts(self) -> None:
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM feed_posts")
        if cur.fetchone()[0] == 0:
            posts = [
                (1, "Excited to launch our local privacy-preserving social graph!", 42),
                (2, "Optimizing SQLite B-tree indices for sub-millisecond friend lookups.", 19),
                (3, "Just connected with 15 fellow researchers in Melbourne.", 28),
                (4, "Why client-side deliberation outperforms centralized cloud feeds.", 55),
                (5, "Running ModernBERT-Large on Apple Silicon MPS with zero thermal throttling.", 73),
            ]
            cur.executemany("INSERT INTO feed_posts (user_id, content, likes_count) VALUES (?, ?, ?)", posts)
            conn.commit()
        conn.close()

    def rank_feed(self, viewer_id: int) -> List[Dict[str, Any]]:
        self.populate_synthetic_posts()
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            friendships = _load_friendship_affinities(cur)
            cur.execute(
                """
                SELECT p.id, p.user_id AS author_id, p.content, p.likes_count,
                       u.name, u.city, u.occupation, p.created_at
                FROM feed_posts p
                JOIN users u ON p.user_id = u.id
                """
            )
            raw_posts = [dict(r) for r in cur.fetchall()]
        finally:
            conn.close()

        now = time.time()
        viewer_friends = friendships.get(viewer_id, {})

        ranked: List[Dict[str, Any]] = []
        for post in raw_posts:
            author_id = post["author_id"]
            relationship = viewer_friends.get(author_id, DISCOVERY_FLOOR)
            social_proof = PROOF_WEIGHT * math.log1p(max(0, int(post["likes_count"]) or 0))
            affinity = REL_WEIGHT * relationship + social_proof

            ptype = classify_post_type(post["content"])
            weight = POST_TYPE_WEIGHTS[ptype]

            age_hours = max(0.0, (now - _parse_created_at(post["created_at"])) / 3600.0)
            decay = 1.0 / (1.0 + DECAY_LAMBDA_PER_HOUR * age_hours)

            score = affinity * weight * decay
            created_epoch = int(_parse_created_at(post["created_at"]))

            ranked.append({
                **post,
                "author_id": author_id,
                "created_ts": created_epoch,
                "post_type": ptype,
                "relationship": relationship,
                "affinity": affinity,
                "weight": weight,
                "decay": decay,
                "edgerank_score": score,
            })

        # Strictly descending by exact score; deterministic tie-breaks.
        ranked.sort(key=lambda p: (-p["edgerank_score"], -p["created_ts"], p["id"]))
        return ranked


if __name__ == "__main__":
    import sys

    db = sys.argv[1] if len(sys.argv) > 1 else "social_network.db"
    viewer = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    feed = FeedRanker(db).rank_feed(viewer)
    print(f"{'rk':>3} {'post':>4} {'author':>6} {'type':>5} {'likes':>5}"
          f" {'affinity':>9} {'weight':>7} {'decay':>7}  score")
    for rank, post in enumerate(feed, start=1):
        print(f"{rank:>3} {post['id']:>4} {post['author_id']:>6} {post['post_type']:>5}"
              f" {post['likes_count']:>5} {post['affinity']:>9.4f} {post['weight']:>7.2f}"
              f" {post['decay']:>7.4f}  {post['edgerank_score']:.6f}")
