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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8088)
