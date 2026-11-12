"""Ingest contacts.csv into the sovereign-edge social network SQLite database.

Source format
-------------
contacts.csv is transposed (one attribute per line, one user per column), with no
header row and exactly 7 lines in canonical order:

    0  names              e.g. "Alice Johnson"
    1  phone numbers      e.g. "0412 345 678"   (NOT persisted - see below)
    2  ages               e.g. "Age: 34"
    3  gender             e.g. "Female"
    4  city               e.g. "Melbourne"
    5  occupation         e.g. "Teacher"
    6  marital status     e.g. "Married"

User N (1-based) is the column at index N-1 across all lines, so ids are stable
across runs even if rows are reordered by re-running this script. This matters:
feed_ranker.py seeds synthetic posts against hard-coded user_ids 1..5.

Schema contract
---------------
users / friendships / feed_posts match exactly what app.py, matching_engine.py,
feed_ranker.py and test_social_suite.py query today. Phone numbers (line 2) are
intentionally not persisted: no consumer reads them and keeping PII out of the
public users table is cheaper than a column nobody selects.

Idempotency
-----------
Re-running drops and rebuilds all three tables in one transaction, so the
database always reflects contacts.csv exactly (users count == CSV columns).
feed_ranker.py re-seeds synthetic posts on demand when feed_posts is empty.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DB_PATH = SCRIPT_DIR / "social_network.db"

# contacts.csv lives at the repo root, above barrage/. Try CWD first so the
# script also works from inside barrage/social_network/, then walk up.
CSV_CANDIDATES: list[Path] = [
    Path.cwd() / "contacts.csv",
    SCRIPT_DIR / "contacts.csv",
    *(ancestor / "contacts.csv" for ancestor in SCRIPT_DIR.parents),
]

# Canonical line order of the transposed CSV. Index 1 is phones: parsed and
# validated below but deliberately not stored (PII minimization).
LINE_FIELDS = ("name", "phone_number", "age", "gender", "city", "occupation", "marital_status")


def find_csv() -> Path:
    for candidate in CSV_CANDIDATES:
        if candidate.is_file():
            return candidate
    tried = ", ".join(str(c) for c in CSV_CANDIDATES)
    raise FileNotFoundError(f"contacts.csv not found. Tried: {tried}")


def parse_contacts(csv_path: Path) -> list[dict]:
    """Parse the transposed contacts.csv into one record per user column."""
    lines = [ln.strip() for ln in csv_path.read_text(encoding="utf-8-sig").splitlines() if ln.strip()]
    if len(lines) != 7:
        raise ValueError(f"expected exactly {len(LINE_FIELDS)} non-empty lines, found {len(lines)}")

    columns = [line.split(",") for line in lines]
    counts = {len(col) for col in columns}
    if len(counts) != 1:
        raise ValueError(f"inconsistent column counts across lines: {sorted(counts)}")
    n_users = counts.pop()
    if n_users == 0:
        raise ValueError("contacts.csv contains no users")

    def field(i: int, j: int) -> str:
        return columns[i][j].strip()

    records = []
    for j in range(n_users):
        age_raw = field(2, j).removeprefix("Age:").strip()  # tolerate bare ints too
        try:
            age = int(age_raw)
        except ValueError as exc:
            raise ValueError(f"user {j + 1}: unparseable age {field(2, j)!r}") from exc

        name = field(0, j)
        gender = field(3, j)
        city = field(4, j)
        occupation = field(5, j)
        marital_status = field(6, j)
        if not all([name, gender, city, occupation, marital_status]):
            raise ValueError(f"user {j + 1}: empty required field")

        records.append({
            "id": j + 1,
            "name": name,
            "age": age,
            "gender": gender,
            "city": city,
            "occupation": occupation,
            "marital_status": marital_status,
        })
    return records


SCHEMA = """
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    age INTEGER NOT NULL,
    gender TEXT,
    city TEXT NOT NULL,
    occupation TEXT NOT NULL,
    marital_status TEXT NOT NULL
);

CREATE TABLE friendships (
    user_id_a INTEGER NOT NULL,
    user_id_b INTEGER NOT NULL,
    affinity_score REAL DEFAULT 0.5,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id_a, user_id_b),
    FOREIGN KEY (user_id_a) REFERENCES users(id),
    FOREIGN KEY (user_id_b) REFERENCES users(id)
);

CREATE TABLE feed_posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    content TEXT NOT NULL,
    likes_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

-- Established indexes from the existing deployment.
CREATE INDEX idx_users_city ON users(city);
CREATE INDEX idx_users_occupation ON users(occupation);
CREATE INDEX idx_friendships_b ON friendships(user_id_b);

-- New: name is the primary social-graph lookup; user_id drives per-user feeds.
CREATE INDEX idx_users_name ON users(name);
CREATE INDEX idx_feed_posts_user_id ON feed_posts(user_id);
"""


def build_database(db_path: Path, records: list[dict]) -> dict:
    """Drop and rebuild all tables in one transaction. Returns row counts."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys=ON")  # enforce FKs during ingest, not just after
    try:
        cur = conn.cursor()
        # One transaction: either the old state or the new one, never a mix.
        with conn:
            for table in ("feed_posts", "friendships", "users"):  # children first
                cur.execute(f"DROP TABLE IF EXISTS {table}")
            cur.executescript(SCHEMA)
            cur.executemany(
                """INSERT INTO users (id, name, age, gender, city, occupation, marital_status)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                [
                    (r["id"], r["name"], r["age"], r["gender"], r["city"],
                     r["occupation"], r["marital_status"])
                    for r in records
                ],
            )
        counts = {table: cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                  for table in ("users", "friendships", "feed_posts")}
    finally:
        conn.close()
    return counts


def main() -> int:
    csv_path = find_csv()
    records = parse_contacts(csv_path)
    counts = build_database(DB_PATH, records)

    expected = len(records)
    print(f"source      : {csv_path}")
    print(f"database    : {DB_PATH}")
    for table in ("users", "friendships", "feed_posts"):
        print(f"{table:<12}: {counts[table]}")

    if counts["users"] != expected:
        print(f"FAIL: expected {expected} users, found {counts['users']}", file=sys.stderr)
        return 1
    print(f"OK: {expected} users inserted and verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
