import sqlite3
import math
from typing import List, Dict, Any

class MatchingEngine:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def calculate_compatibility(self, u1: Dict[str, Any], u2: Dict[str, Any]) -> float:
        """
        Multivariate Affinity Score:
        - City Proximity: 0.35 weight
        - Occupation Alignment: 0.25 weight
        - Age Delta Gaussian Decay: 0.25 weight (exp(-|age1 - age2| / 12))
        - Baseline Social Entropy: 0.15 weight
        """
        score = 0.0

        # City match
        if u1["city"] == u2["city"]:
            score += 0.35
        
        # Occupation match
        if u1["occupation"] == u2["occupation"]:
            score += 0.25
        
        # Age proximity
        age_diff = abs(u1["age"] - u2["age"])
        age_score = math.exp(-age_diff / 12.0)
        score += 0.25 * age_score

        # Social baseline
        score += 0.15
        return round(min(1.0, score), 4)

    def find_matches(self, user_id: int, top_k: int = 5) -> List[Dict[str, Any]]:
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        target = cur.fetchone()
        if not target:
            conn.close()
            return []

        cur.execute("SELECT * FROM users WHERE id != ?", (user_id,))
        candidates = [dict(r) for r in cur.fetchall()]
        conn.close()

        target_dict = dict(target)
        scored = []
        for cand in candidates:
            aff = self.calculate_compatibility(target_dict, cand)
            scored.append({
                "candidate_id": cand["id"],
                "name": cand["name"],
                "age": cand["age"],
                "city": cand["city"],
                "occupation": cand["occupation"],
                "affinity_score": aff
            })

        scored.sort(key=lambda x: -x["affinity_score"])
        return scored[:top_k]