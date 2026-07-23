from __future__ import annotations

from price_monitor.adapters.amazon import AmazonAdapter
from price_monitor.adapters.instacart import InstacartAdapter
from price_monitor.adapters.safeway import SafewayAdapter
from price_monitor.adapters.target import TargetAdapter
from price_monitor.adapters.walmart import WalmartAdapter

_ADAPTERS = {
    "amazon": AmazonAdapter(),
    "safeway": SafewayAdapter(),
    "instacart": InstacartAdapter(),
    "target": TargetAdapter(),
    "walmart": WalmartAdapter(),
}


def get_adapter(name: str):
    key = (name or "").strip().lower()
    if key not in _ADAPTERS:
        raise ValueError(f"Unknown retailer: {name}. Use: {', '.join(_ADAPTERS)}")
    return _ADAPTERS[key]


def list_retailers() -> list[str]:
    return list(_ADAPTERS.keys())
