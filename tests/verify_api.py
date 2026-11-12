"""Verification harness for the Social Network FastAPI app.

Runs the ASGI app in-process via TestClient and asserts:
  * every endpoint returns 200 with a valid payload,
  * missing entities return 404,
  * friend lists match what is actually stored in SQLite (both edge directions).

Two passes:
  1. Live DB   - read-only assertions against social_network.db.
  2. Seeded DB - hermetic temp database with known users + friendship edges,
                 proving the friends endpoint end-to-end (the live friendships
                 table is intentionally empty: ingest_db.py only ingests users;
                 matching_engine computes matches in memory and never persists).
"""
import os
import sqlite3
import sys
import tempfile

REPO_DIR = os.path.dirname(os.path.abspath(__file__)) if os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py")) else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_DIR)

from fastapi.testclient import TestClient

import app as social_app
from app import DB_PATH, get_db

client = TestClient(social_app.app)
PASS_COUNT = 0


def check(label: str, cond: bool):
    global PASS_COUNT
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {label}")
    assert cond, f"assertion failed: {label}"
    PASS_COUNT += 1


# --- 1. /api/health ------------------------------------------------------- #
r = client.get("/api/health")
check("GET /api/health -> 200", r.status_code == 200)
body = r.json()
check("health status healthy", body["status"] == "healthy")
check("health reports reachable DB", body["database_reachable"] is True)

# --- 2. /api/users (listing + filters) ------------------------------------ #
conn = get_db()
total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
city_row = conn.execute(
    "SELECT city, COUNT(*) AS n FROM users WHERE city IS NOT NULL GROUP BY city LIMIT 1"
).fetchone()
check("live DB has users", total_users >= 200)

r = client.get("/api/users")
check("GET /api/users -> 200", r.status_code == 200)
body = r.json()
check("users listing count field consistent", body["count"] == len(body["users"]))
check("default limit <= 50 and non-empty", 1 <= body["count"] <= 50)

r = client.get("/api/users", params={"limit": 7})
check("limit=7 honored", r.status_code == 200 and r.json()["count"] == 7)

r = client.get(f"/api/users?limit=5&city={city_row['city']}")
expected_city_count = min(5, city_row["n"])
check(
    f"city filter ({city_row['city']}) -> 200 and correct count",
    r.status_code == 200 and r.json()["count"] == expected_city_count,
)

# --- 3. /api/users/{id} ---------------------------------------------------- #
sample = conn.execute("SELECT * FROM users ORDER BY id LIMIT 1").fetchone()
sample_id = sample["id"]

r = client.get(f"/api/users/{sample_id}")
check(f"GET /api/users/{sample_id} -> 200", r.status_code == 200)
body = r.json()
check("user id echoed", body["id"] == sample_id)
check("user name matches DB", body["name"] == sample["name"])
for field in ("age", "city", "occupation", "marital_status"):
    check(f"user {field} matches DB", str(body[field]) == str(sample[field]))

r = client.get("/api/users/999999")
check("GET /api/users/999999 -> 404", r.status_code == 404)

# --- 4. /api/users/{id}/friends against live DB --------------------------- #
expected_live = {
    row["user_id_b"] if row["user_id_a"] == sample_id else row["user_id_a"]
    for row in conn.execute(
        "SELECT user_id_a, user_id_b FROM friendships"
        " WHERE user_id_a = ? OR user_id_b = ?",
        (sample_id, sample_id),
    )
}
conn.close()

r = client.get(f"/api/users/{sample_id}/friends")
check(f"GET /api/users/{sample_id}/friends -> 200", r.status_code == 200)
body = r.json()
check("live friend set matches DB (may be empty by design)",
      {f["id"] for f in body["friends"]} == expected_live)

r = client.get("/api/users/999999/friends")
check("GET /api/users/999999/friends -> 404", r.status_code == 404)


# --- 5. Hermetic seeded-DB pass (proves friends endpoint with real edges) -- #
def build_seeded_db(path: str):
    c = sqlite3.connect(path)
    c.execute("PRAGMA foreign_keys=ON")
    c.executescript(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
            age INTEGER NOT NULL, gender TEXT, city TEXT NOT NULL,
            occupation TEXT NOT NULL, marital_status TEXT NOT NULL);
        CREATE TABLE friendships (
            user_id_a INTEGER NOT NULL, user_id_b INTEGER NOT NULL,
            affinity_score REAL DEFAULT 0.5,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id_a, user_id_b));
        """
    )
    users = [
        (1, "Ava Chen", 34, "Female", "Sydney", "Engineer", "Single"),
        (2, "Ben Roy", 41, "Male", "Melbourne", "Doctor", "Married"),
        (3, "Cara Lin", 29, "Female", "Sydney", "Designer", "Single"),
        (4, "Dan Fox", 52, "Male", "Brisbane", "Lawyer", "Divorced"),
    ]
    c.executemany("INSERT INTO users VALUES (?, ?, ?, ?, ?, ?, ?)", users)
    # Note: edges stored in mixed directions to exercise both query arms.
    edges = [
        (1, 2, 0.9),   # a < b
        (3, 1, 0.7),   # a > b  (user 1 is user_id_b here)
        (4, 2, 0.5),
    ]
    c.executemany("INSERT INTO friendships (user_id_a, user_id_b, affinity_score)"
                  " VALUES (?, ?, ?)", edges)
    c.commit()
    c.close()


tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
tmp.close()
build_seeded_db(tmp.name)

social_app.DB_PATH = tmp.name  # app reads DB_PATH at call time via get_db()
try:
    r = client.get("/api/users/1/friends")
    check("seeded: GET /api/users/1/friends -> 200", r.status_code == 200)
    body = r.json()
    got = {f["id"]: f for f in body["friends"]}
    check("seeded: user 1 friends are exactly {2,3} (both edge directions)",
          set(got) == {2, 3})
    check("seeded: count field correct", body["count"] == 2 and len(body["friends"]) == 2)
    check("seeded: affinity scores round-trip",
          got[2]["affinity_score"] == 0.9 and got[3]["affinity_score"] == 0.7)
    check("seeded: sorted by affinity desc",
          [f["id"] for f in body["friends"]] == [2, 3])

    r = client.get("/api/users/4/friends")
    check("seeded: GET /api/users/4/friends -> 200 and edge from user_id_a side",
          r.status_code == 200 and {f["id"] for f in r.json()["friends"]} == {2})

    r = client.get("/api/users/999999/friends")
    check("seeded: GET /api/users/999999/friends -> 404", r.status_code == 404)
finally:
    os.unlink(tmp.name)

print(f"\nALL {PASS_COUNT} CHECKS PASSED")
