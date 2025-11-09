thonfrom typing import Any, Dict, List, Optional, Union

def extract_menu_items(root: Union[Dict[str, Any], List[Any]]) -> List[Dict[str, Any]]:
    """
    Uber Eats' internal menu structure is not public. This function walks the
    JSON tree and extracts objects that look like menu items.

    A "menu item" is heuristically defined as a dict that has:
      - a 'title' or 'name'
      - some kind of price information
    """
    items: List[Dict[str, Any]] = []

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            if _looks_like_menu_item(node):
                normalized = _normalize_menu_item(node)
                if normalized:
                    items.append(normalized)
            for value in node.values():
                _walk(value)
        elif isinstance(node, list):
            for element in node:
                _walk(element)

    _walk(root)
    return items

def _looks_like_menu_item(node: Dict[str, Any]) -> bool:
    has_title = isinstance(node.get("title") or node.get("name"), str)

    has_price = any(
        key in node
        for key in (
            "price",
            "priceString",
            "displayPrice",
            "unitPrice",
        )
    )
    return bool(has_title and has_price)

def _extract_price(node: Dict[str, Any]) -> Optional[float]:
    price_raw = None
    for key in ("price", "unitPrice", "amount"):
        if key in node:
            price_raw = node[key]
            break

    if price_raw is None:
        # Try parsing from a price-like string
        price_str = (
            node.get("priceString")
            or node.get("displayPrice")
            or node.get("formattedPrice")
        )
        if isinstance(price_str, str):
            digits = "".join(ch for ch in price_str if ch.isdigit() or ch == ".")
            if digits:
                try:
                    return float(digits)
                except ValueError:
                    return None
        return None

    try:
        if isinstance(price_raw, (int, float)):
            # Some APIs return cents
            if price_raw > 10000: