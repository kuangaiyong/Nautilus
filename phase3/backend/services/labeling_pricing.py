"""
Labeling job pricing.

Per-item pricing for data-labeling tasks with a consensus-agent multiplier.
Total = price_per_item * items * agents. Prices come from the single source of
truth in services/pricing.py (the labeling subset of TASK_PRICES); this service
only adds the quote/breakdown shape consumed by api/labeling_service.py.
"""
from typing import Dict

from services import pricing

# Per-item fallback for an unknown labeling type (RMB), mirrors a mid-range tag.
DEFAULT_PRICE_PER_ITEM = 0.25


class LabelingPricingService:
    """Quote and price-list helpers for labeling jobs."""

    @staticmethod
    def price_per_item(labeling_type: str) -> float:
        return pricing.TASK_PRICES.get(labeling_type, DEFAULT_PRICE_PER_ITEM)

    def get_price_quote(self, labeling_type: str, items: int, agents: int = 3) -> Dict:
        per_item = self.price_per_item(labeling_type)
        subtotal = per_item * items
        total = subtotal * agents
        return {
            "labeling_type": labeling_type,
            "items": items,
            "agents": agents,
            "price_per_item": round(per_item, 2),
            "subtotal": round(subtotal, 2),
            "total": round(total, 2),
            "currency": "RMB",
        }

    def list_prices(self) -> Dict[str, float]:
        return pricing.get_price_list()["labeling_per_item"]
