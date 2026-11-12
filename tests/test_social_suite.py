import pytest
import sqlite3
import os
import sys

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_DIR)
sys.path.append(os.path.expanduser("~/test"))

from matching_engine import MatchingEngine
from feed_ranker import FeedRanker
from monetization import MonetizationEngine

DB_PATH = os.path.join(REPO_DIR, "social_network.db")

def test_database_record_count():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM users")
    count = cur.fetchone()[0]
    conn.close()
    assert count >= 200

def test_matching_engine_bounded_scores():
    engine = MatchingEngine(DB_PATH)
    matches = engine.find_matches(1, top_k=5)
    assert len(matches) == 5
    for m in matches:
        assert 0.0 <= m["affinity_score"] <= 1.0

def test_feed_ranking_descending():
    ranker = FeedRanker(DB_PATH)
    feed = ranker.rank_feed(1)
    assert len(feed) >= 5
    scores = [p["edgerank_score"] for p in feed]
    assert scores == sorted(scores, reverse=True)

def test_monetization_edge_savings():
    engine = MonetizationEngine(206)
    m = engine.calculate_cloud_vs_edge()
    assert m["savings_percent"] > 80.0
    assert m["edge_net_monthly_profit"] > 0.0