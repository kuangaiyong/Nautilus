"""
Tests for services.labeling_pricing (LabelingPricingService).

Pure pricing math over the real services/pricing.py table — no mocks, no DB.
Backs the restored module lazily imported by api/labeling_service.py
(/labeling/quote and /labeling/prices).
"""
from services.labeling_pricing import LabelingPricingService, DEFAULT_PRICE_PER_ITEM
from services import pricing


def test_quote_total_is_per_item_times_items_times_agents():
    svc = LabelingPricingService()
    q = svc.get_price_quote("sentiment", items=100, agents=3)
    assert q["price_per_item"] == pricing.TASK_PRICES["sentiment"]  # 0.15
    assert q["subtotal"] == round(0.15 * 100, 2)
    assert q["total"] == round(0.15 * 100 * 3, 2)
    assert q["currency"] == "RMB"


def test_quote_default_agents_is_three():
    svc = LabelingPricingService()
    q = svc.get_price_quote("object_detection", items=10)
    assert q["agents"] == 3
    assert q["total"] == round(pricing.TASK_PRICES["object_detection"] * 10 * 3, 2)


def test_unknown_type_uses_default_price():
    svc = LabelingPricingService()
    q = svc.get_price_quote("not_a_real_type", items=5, agents=2)
    assert q["price_per_item"] == DEFAULT_PRICE_PER_ITEM
    assert q["total"] == round(DEFAULT_PRICE_PER_ITEM * 5 * 2, 2)


def test_list_prices_returns_labeling_subset():
    svc = LabelingPricingService()
    prices = svc.list_prices()
    assert prices["sentiment"] == 0.15
    assert prices["classification"] == 0.25
    # academic / simulation types must not leak into the labeling list
    assert "curve_fitting" not in prices
    assert "motion_planning" not in prices
