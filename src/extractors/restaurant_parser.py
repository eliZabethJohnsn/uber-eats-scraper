thonimport json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union

import requests
from bs4 import BeautifulSoup

from .menu_parser import extract_menu_items
from .utils_location import parse_hours, parse_location

logger = logging.getLogger(__name__)

@dataclass
class RestaurantParserConfig:
    user_agent: str
    timeout: int
    verify_ssl: bool
    max_attempts: int
    backoff_seconds: float

class RestaurantParser:
    """
    Responsible for fetching an Uber Eats restaurant page and extracting
    a normalized restaurant document.
    """

    def __init__(
        self,
        user_agent: Optional[str] = None,
        timeout: int = 15,
        verify_ssl: bool = True,
        max_attempts: int = 3,
        backoff_seconds: float = 2.0,
    ) -> None:
        self.config = RestaurantParserConfig(
            user_agent=user_agent
            or (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            ),
            timeout=timeout,
            verify_ssl=verify_ssl,
            max_attempts=max_attempts,
            backoff_seconds=backoff_seconds,
        )

    # Public API -------------------------------------------------------------

    def scrape_restaurant(
        self,
        url: str,
        include_menu: bool = True,
    ) -> Optional[Dict[str, Any]]:
        html = self._fetch_with_retries(url)
        if html is None:
            return None

        try:
            soup = BeautifulSoup(html, "html.parser")
            payload = self._extract_json_payload(soup)
            restaurant = self._build_restaurant_document(
                url=url,
                payload=payload,
                include_menu=include_menu,
            )
            return restaurant
        except Exception as exc:
            logger.error("Failed to parse page for %s: %s", url, exc, exc_info=True)
            return None

    # HTTP handling ----------------------------------------------------------

    def _fetch_with_retries(self, url: str) -> Optional[str]:
        session = requests.Session()
        headers = {
            "User-Agent": self.config.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }

        last_error: Optional[Exception] = None
        for attempt in range(1, self.config.max_attempts + 1):
            try:
                logger.debug("Fetching %s (attempt %d)", url, attempt)
                resp = session.get(
                    url,
                    headers=headers,
                    timeout=self.config.timeout,
                    verify=self.config.verify_ssl,
                )
                resp.raise_for_status()
                return resp.text
            except Exception as exc:  # pragma: no cover - network dependent
                last_error = exc
                logger.warning(
                    "Request to %s failed on attempt %d/%d: %s",
                    url,
                    attempt,
                    self.config.max_attempts,
                    exc,
                )
                if attempt < self.config.max_attempts:
                    time.sleep(self.config.backoff_seconds)

        logger.error("All attempts failed for %s: %s", url, last_error)
        return None

    # JSON extraction --------------------------------------------------------

    def _extract_json_payload(self, soup: BeautifulSoup) -> Dict[str, Any]:
        """
        Many modern web apps embed a big JSON payload inside <script> tags.
        This attempts to locate and parse such a blob.
        """
        # First try script tags that look like application/json
        scripts = soup.find_all("script")
        for script in scripts:
            script_type = script.get("type", "")
            text = script.string or script.get_text(strip=True)
            if not text:
                continue

            if "application/json" in script_type:
                payload = self._try_parse_json_from_text(text)
                if payload is not None:
                    logger.debug("Parsed JSON from application/json script tag")
                    return payload

        # Fallback: search for window.__NUXT__ or window.__INITIAL_STATE__
        full_html = soup.prettify()
        nuxt_match = re.search(r"window\.__NUXT__\s*=\s*(\{.*?\})\s*;", full_html, re.S)
        if nuxt_match:
            payload = self._try_parse_json_from_text(nuxt_match.group(1))
            if payload is not None:
                logger.debug("Parsed JSON from window.__NUXT__ variable")
                return payload

        # Final fallback: first big JSON-looking block inside any script tag
        for script in scripts:
            text = script.string or script.get_text(strip=True)
            if not text:
                continue
            payload = self._try_parse_json_from_text(text)
            if payload is not None:
                logger.debug("Parsed JSON from generic script block")
                return payload

        raise ValueError("Unable to extract JSON payload from page")

    @staticmethod
    def _try_parse_json_from_text(text: str) -> Optional[Dict[str, Any]]:
        text = text.strip()
        # Simple direct parse first
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass

        # Try to extract the first top-level JSON object
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidate = text[start : end + 1]
            try:
                obj = json.loads(candidate)
                if isinstance(obj, dict):
                    return obj
            except Exception:
                return None

        return None

    # Document building ------------------------------------------------------

    def _build_restaurant_document(
        self,
        url: str,
        payload: Dict[str, Any],
        include_menu: bool,
    ) -> Dict[str, Any]:
        """
        Uber Eats' internal JSON structure is not documented and can change.
        This function uses heuristics to locate restaurant fields inside the
        payload while remaining robust to minor schema changes.
        """
        root = payload

        restaurant_node = self._guess_restaurant_node(root)
        logo_url = self._find_first_value(root, ["logoUrl", "heroImage", "imageUrl"])
        name = self._find_first_value(restaurant_node, ["name", "title"])
        categories = self._guess_categories(restaurant_node or root)
        rating = self._find_first_value(
            restaurant_node or root, ["rating", "averageRating"]
        )
        review_count = self._find_first_value(
            restaurant_node or root, ["reviewCount", "ratingCount"]
        )
        currency_code = self._find_first_value(
            restaurant_node or root, ["currencyCode", "currency"]
        )
        uuid = self._find_first_value(
            restaurant_node or root, ["uuid", "storeUuid", "restaurantUuid"]
        )

        location = parse_location(restaurant_node or root)
        hours = parse_hours(restaurant_node or root)

        menu_items: List[Dict[str, Any]] = []
        if include_menu:
            menu_items = extract_menu_items(restaurant_node or root)

        document: Dict[str, Any] = {
            "logoUrl": logo_url,
            "name": name or "",
            "categories": categories,
            "rating": rating,
            "reviewCount": review_count,
            "currencyCode": currency_code,
            "location": location,
            "phoneNumber": self._find_first_value(
                restaurant_node or root, ["phoneNumber", "phone", "contactPhone"]
            ),
            "uuid": uuid,
            "hours": hours,
            "url": url,
            "menuItems": menu_items,
        }

        return document

    # Heuristics helpers -----------------------------------------------------

    def _guess_restaurant_node(
        self, root: Union[Dict[str, Any], List[Any]]
    ) -> Optional[Dict[str, Any]]:
        """
        Tries to find a dict that looks like the main restaurant object.
        """
        candidates: List[Dict[str, Any]] = []

        def _walk(node: Any) -> None:
            if isinstance(node, dict):
                if any(
                    key in node
                    for key in (
                        "storeUuid",
                        "restaurantUuid",
                        "restaurantId",
                        "merchant",
                    )
                ):
                    candidates.append(node)
                for value in node.values():
                    _walk(value)
            elif isinstance(node, list):
                for item in node:
                    _walk(item)

        _walk(root)
        if candidates:
            logger.debug("Found %d potential restaurant nodes, using first", len(candidates))
            return candidates[0]
        return root if isinstance(root, dict) else None

    def _find_first_value(
        self,
        node: Optional[Union[Dict[str, Any], List[Any]]],
        keys: List[str],
    ) -> Any:
        if node is None:
            return None

        def _walk(n: Any) -> Any:
            if isinstance(n, dict):
                for k in keys:
                    if k in n:
                        return n[k]
                for v in n.values():
                    found = _walk(v)
                    if found is not None:
                        return found
            elif isinstance(n, list):
                for item in n:
                    found = _walk(item)
                    if found is not None:
                        return found
            return None

        return _walk(node)

    def _guess_categories(
        self, node: Union[Dict[str, Any], List[Any]]
    ) -> List[str]:
        # Direct categories list
        def _walk(n: Any) -> Optional[List[str]]:
            if isinstance(n, dict):
                if "categories" in n and isinstance(n["categories"], list):
                    raw = n["categories"]
                    if all(isinstance(v, str) for v in raw):
                        return raw
                    # Sometimes categories are objects
                    titles: List[str] = []
                    for item in raw:
                        if isinstance(item, dict):
                            title = item.get("title") or item.get("name")
                            if isinstance(title, str):
                                titles.append(title)
                    if titles:
                        return titles

                for v in n.values():
                    result = _walk(v)
                    if result:
                        return result

            elif isinstance(n, list):
                for item in n:
                    result = _walk(item)
                    if result:
                        return result
            return None

        found = _walk(node)
        return found or []