"""Sovereign Social Network API (FastAPI).

Read-only REST layer over the local SQLite database.

Endpoints:
    GET /api/health                -> service + DB liveness
    GET /api/users?limit=&city=   -> paginated user listing
    GET /api/users/{user_id}      -> single user record
    GET /api/users/{user_id}/friends -> friend list with affinity scores

All queries are parameterized; all request inputs and response payloads are
validated by Pydantic models.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Path, Query
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from feed_ranker import FeedRanker
from matching_engine import MatchingEngine

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "social_network.db")
TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")

app = FastAPI(title="Sovereign Social Network API", version="2.0.0")


# --------------------------------------------------------------------------- #
# Pydantic models (typed request/response validation)
# --------------------------------------------------------------------------- #
class HealthStatus(BaseModel):
    status: str
    engine: str
    database_reachable: bool
    checked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class UserOut(BaseModel):
    id: int
    name: str
    age: int = Field(ge=0, le=130)
    gender: Optional[str] = None
    city: str
    occupation: str
    marital_status: str


class UsersResponse(BaseModel):
    count: int
    users: list[UserOut]


class FriendOut(UserOut):
    affinity_score: float = Field(ge=0.0, le=1.0)
    created_at: Optional[str] = None


class FriendsResponse(BaseModel):
    user_id: int
    count: int
    friends: list[FriendOut]


class CreatePostIn(BaseModel):
    user_id: int = Field(ge=1)
    content: str = Field(min_length=1, max_length=500)


class PostOut(BaseModel):
    id: int
    user_id: int
    content: str
    likes_count: int
    created_at: str


class ConnectIn(BaseModel):
    user_id_a: int = Field(ge=1)
    user_id_b: int = Field(ge=1)


class ConnectOut(BaseModel):
    status: str
    user_id_a: int
    user_id_b: int
    affinity_score: float


class ProfileOut(BaseModel):
    id: int
    name: str
    age: int
    gender: Optional[str] = None
    city: str
    occupation: str
    marital_status: str
    friends_count: int
    posts_count: int
    match_avg: float


# --------------------------------------------------------------------------- #
# DB helpers
# --------------------------------------------------------------------------- #
def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _user_row_to_model(row: sqlite3.Row) -> UserOut:
    data = dict(row)
    if "affinity_score" not in data and "created_at" not in data:
        return UserOut.model_validate(data)
    return FriendOut.model_validate(data)


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@app.get("/", response_class=FileResponse, summary="Serve Sovereign Web UI")
def serve_index() -> FileResponse:
    index_path = os.path.join(TEMPLATES_DIR, "index.html")
    if not os.path.exists(index_path):
        raise HTTPException(status_code=404, detail="Web UI template not found")
    return FileResponse(index_path, media_type="text/html")


@app.get("/api/health", response_model=HealthStatus, summary="Service health check")
def health() -> HealthStatus:
    database_reachable = False
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'")
        database_reachable = cur.fetchone() is not None
        conn.close()
    except sqlite3.Error:
        database_reachable = False
    return HealthStatus(
        status="healthy",
        engine="system2_edge",
        database_reachable=database_reachable,
    )


@app.get("/api/users", response_model=UsersResponse, summary="List users")
def get_users(
    limit: int = Query(default=50, ge=1, le=500),
    city: Optional[str] = Query(default=None, max_length=200),
) -> UsersResponse:
    conn = get_db()
    try:
        cur = conn.cursor()
        if city is not None:
            cur.execute(
                "SELECT id, name, age, gender, city, occupation, marital_status"
                " FROM users WHERE city = ? ORDER BY id LIMIT ?",
                (city, limit),
            )
        else:
            cur.execute(
                "SELECT id, name, age, gender, city, occupation, marital_status"
                " FROM users ORDER BY id LIMIT ?",
                (limit,),
            )
        rows = [UserOut.model_validate(dict(r)) for r in cur.fetchall()]
    finally:
        conn.close()
    return UsersResponse(count=len(rows), users=rows)


@app.get("/api/users/{user_id}", response_model=UserOut, summary="Get a user by id")
def get_user(user_id: int = Path(..., ge=1)) -> UserOut:
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, name, age, gender, city, occupation, marital_status"
            f" FROM users WHERE id = ?",
            (user_id,),
        )
        row = cur.fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")
    return UserOut.model_validate(dict(row))


_FRIEND_COLUMNS = (
    "u.id, u.name, u.age, u.gender, u.city, u.occupation, u.marital_status,"
    " f.affinity_score, f.created_at"
)


@app.get(
    "/api/users/{user_id}/friends",
    response_model=FriendsResponse,
    summary="List a user's friends",
)
def get_user_friends(user_id: int = Path(..., ge=1)) -> FriendsResponse:
    conn = get_db()
    try:
        cur = conn.cursor()

        cur.execute("SELECT id FROM users WHERE id = ?", (user_id,))
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail=f"User {user_id} not found")

        # Friendship edges are stored as unordered pairs; query both directions.
        cur.execute(
            f"SELECT {_FRIEND_COLUMNS} FROM friendships f JOIN users u ON u.id = f.user_id_b"
            " WHERE f.user_id_a = ? UNION ALL "
            f"SELECT {_FRIEND_COLUMNS} FROM friendships f JOIN users u ON u.id = f.user_id_a"
            " WHERE f.user_id_b = ? ORDER BY affinity_score DESC",
            (user_id, user_id),
        )
        rows = [FriendOut.model_validate(dict(r)) for r in cur.fetchall()]
    finally:
        conn.close()
    return FriendsResponse(user_id=user_id, count=len(rows), friends=rows)


@app.get("/api/feed", summary="EdgeRank Ranked Feed")
def get_feed(
    viewer_id: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict:
    ranker = FeedRanker(DB_PATH)
    posts = ranker.rank_feed(viewer_id=viewer_id)[:limit]
    return {"viewer_id": viewer_id, "count": len(posts), "feed": posts}


@app.get("/api/matches", summary="Multivariate Contact Matches")
def get_matches(
    user_id: int = Query(default=1, ge=1),
    top_k: int = Query(default=5, ge=1, le=50),
) -> dict:
    engine = MatchingEngine(DB_PATH)
    matches = engine.find_matches(user_id=user_id, top_k=top_k)
    return {"user_id": user_id, "count": len(matches), "matches": matches}


@app.get("/api/me", response_model=ProfileOut, summary="Get active viewer profile")
def get_me(user_id: int = Query(default=82, ge=1)) -> ProfileOut:
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, name, age, gender, city, occupation, marital_status"
            " FROM users WHERE id = ?",
            (user_id,),
        )
        user_row = cur.fetchone()
        if user_row is None:
            raise HTTPException(status_code=404, detail=f"User {user_id} not found")

        cur.execute(
            "SELECT COUNT(*) FROM friendships WHERE user_id_a = ? OR user_id_b = ?",
            (user_id, user_id),
        )
        friends_count = cur.fetchone()[0]

        cur.execute(
            "SELECT COUNT(*) FROM feed_posts WHERE user_id = ?",
            (user_id,),
        )
        posts_count = cur.fetchone()[0]
    finally:
        conn.close()

    engine = MatchingEngine(DB_PATH)
    matches = engine.find_matches(user_id=user_id, top_k=5)
    match_avg = (
        round(sum(m["affinity_score"] for m in matches) / len(matches) * 100, 1)
        if matches
        else 95.0
    )

    data = dict(user_row)
    data["friends_count"] = friends_count
    data["posts_count"] = posts_count
    data["match_avg"] = match_avg
    return ProfileOut.model_validate(data)


@app.post("/api/posts", response_model=PostOut, status_code=201, summary="Create a feed post")
def create_post(payload: CreatePostIn) -> PostOut:
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM users WHERE id = ?", (payload.user_id,))
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail=f"User {payload.user_id} not found")

        cur.execute(
            "INSERT INTO feed_posts (user_id, content, likes_count, created_at)"
            " VALUES (?, ?, 0, CURRENT_TIMESTAMP)",
            (payload.user_id, payload.content),
        )
        post_id = cur.lastrowid
        conn.commit()

        cur.execute(
            "SELECT id, user_id, content, likes_count, created_at FROM feed_posts WHERE id = ?",
            (post_id,),
        )
        row = cur.fetchone()
    finally:
        conn.close()

    return PostOut.model_validate(dict(row))


@app.post("/api/connect", response_model=ConnectOut, summary="Connect two users as friends")
def connect_users(payload: ConnectIn) -> ConnectOut:
    if payload.user_id_a == payload.user_id_b:
        raise HTTPException(status_code=400, detail="Cannot connect a user with themselves")

    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, city, occupation, age FROM users WHERE id = ?", (payload.user_id_a,))
        u1 = cur.fetchone()
        cur.execute("SELECT id, city, occupation, age FROM users WHERE id = ?", (payload.user_id_b,))
        u2 = cur.fetchone()

        if u1 is None or u2 is None:
            raise HTTPException(status_code=404, detail="One or both users not found")

        engine = MatchingEngine(DB_PATH)
        affinity = engine.calculate_compatibility(dict(u1), dict(u2))

        # Store unordered friendship pair consistently (min_id, max_id)
        id_a, id_b = sorted([payload.user_id_a, payload.user_id_b])
        cur.execute(
            "INSERT OR REPLACE INTO friendships (user_id_a, user_id_b, affinity_score, created_at)"
            " VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
            (id_a, id_b, affinity),
        )
        conn.commit()
    finally:
        conn.close()

    return ConnectOut(
        status="connected",
        user_id_a=payload.user_id_a,
        user_id_b=payload.user_id_b,
        affinity_score=affinity,
    )


@app.post("/api/posts/{post_id}/like", summary="Like a feed post")
def like_post(post_id: int = Path(..., ge=1)) -> dict:
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT likes_count FROM feed_posts WHERE id = ?", (post_id,))
        row = cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"Post {post_id} not found")

        new_count = row["likes_count"] + 1
        cur.execute(
            "UPDATE feed_posts SET likes_count = ? WHERE id = ?",
            (new_count, post_id),
        )
        conn.commit()
    finally:
        conn.close()

    return {"id": post_id, "likes_count": new_count}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8088)
