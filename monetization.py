class MonetizationEngine:
    def __init__(self, monthly_active_users: int = 206):
        self.mau = monthly_active_users

    def calculate_cloud_vs_edge(self) -> dict:
        """
        Calculates 3-year TCO of Centralized Cloud vs System 2 Sovereign Edge.
        """
        # Centralized Cloud Architecture (EC2 + RDS + Bandwidth + Cloud LLM)
        cloud_compute_monthly = 140.0
        cloud_rds_monthly = 95.0
        cloud_llm_inference_monthly = self.mau * 0.45 # $0.45/user for cloud frontier API calls
        cloud_monthly = cloud_compute_monthly + cloud_rds_monthly + cloud_llm_inference_monthly
        cloud_3yr = cloud_monthly * 36

        # System 2 Sovereign Edge Architecture (Local SQLite + Edge Quantization)
        # Server cost is minimal static CDN/relay ($15/mo) + local edge execution ($0 API cost)
        edge_monthly = 15.0
        edge_3yr = edge_monthly * 36

        savings = cloud_3yr - edge_3yr
        savings_pct = (savings / cloud_3yr) * 100

        # Ad revenue model: $12 CPM with 5 ad impressions/day per user
        monthly_impressions = self.mau * 5 * 30
        ad_revenue_monthly = (monthly_impressions / 1000.0) * 12.0

        return {
            "cloud_3yr_cost": round(cloud_3yr, 2),
            "edge_3yr_cost": round(edge_3yr, 2),
            "total_3yr_savings": round(savings, 2),
            "savings_percent": round(savings_pct, 1),
            "monthly_ad_revenue": round(ad_revenue_monthly, 2),
            "edge_net_monthly_profit": round(ad_revenue_monthly - edge_monthly, 2)
        }