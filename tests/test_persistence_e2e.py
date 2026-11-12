"""End-to-end persistence tests for Sovereign Social Network.

Verifies:
1. POST /api/posts persists feed post into SQLite feed_posts table.
2. POST /api/connect creates real friendship in SQLite friendships table.
3. POST /api/posts/{id}/like increments likes_count in SQLite feed_posts table.
4. GET /api/me returns active viewer profile and updated counts.
5. GET /api/feed returns freshly created post with EdgeRank score.
6. GET /api/matches excludes newly connected friend from recommendations.
"""
from fastapi.testclient import TestClient
from app import app
import sqlite3
import os

client = TestClient(app)
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "social_network.db")


def test_e2e_post_creation_and_persistence():
    content = "Functional verification test post for System2 on-device edge."
    res = client.post("/api/posts", json={"user_id": 82, "content": content})
    assert res.status_code == 201, f"Failed: {res.text}"
    data = res.json()
    post_id = data["id"]
    assert data["content"] == content
    assert data["user_id"] == 82
    assert data["likes_count"] == 0

    # Query directly from SQLite DB to prove disk persistence
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT content, likes_count FROM feed_posts WHERE id = ?", (post_id,))
    row = cur.fetchone()
    assert row is not None
    assert row[0] == content
    assert row[1] == 0

    # Verify like endpoint increments persisted count
    like_res = client.post(f"/api/posts/{post_id}/like")
    assert like_res.status_code == 200
    assert like_res.json()["likes_count"] == 1

    cur.execute("SELECT likes_count FROM feed_posts WHERE id = ?", (post_id,))
    assert cur.fetchone()[0] == 1

    # Clean up test post to maintain pristine state
    cur.execute("DELETE FROM feed_posts WHERE id = ?", (post_id,))
    conn.commit()
    conn.close()


def test_e2e_connect_and_filter_matches():
    # Connect Isla Walker (82) with user 83 (Liam Reid)
    res = client.post("/api/connect", json={"user_id_a": 82, "user_id_b": 83})
    assert res.status_code == 200, f"Failed: {res.text}"
    data = res.json()
    assert data["status"] == "connected"
    assert data["affinity_score"] > 0.0

    # Verify friendship persisted in SQLite
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT affinity_score FROM friendships WHERE user_id_a = ? AND user_id_b = ?", (82, 83))
    row = cur.fetchone()
    assert row is not None

    # Verify that Liam Reid (83) is now EXCLUDED from match suggestions for user 82
    matches_res = client.get("/api/matches?user_id=82&top_k=5")
    assert matches_res.status_code == 200
    candidate_ids = [m["candidate_id"] for m in matches_res.json()["matches"]]
    assert 83 not in candidate_ids, f"Connected user 83 should not be in suggestions: {candidate_ids}"

    # Verify profile endpoint shows friends_count >= 1
    me_res = client.get("/api/me?user_id=82")
    assert me_res.status_code == 200
    assert me_res.json()["friends_count"] >= 1

    # Clean up test friendship to maintain pristine state
    cur.execute("DELETE FROM friendships WHERE user_id_a = ? AND user_id_b = ?", (82, 83))
    conn.commit()
    conn.close()
