thonimport argparse
import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure local src modules are importable when running `python src/main.py`
CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from extractors.restaurant_parser import RestaurantParser  # type: ignore
from outputs.json_exporter import export_to_file  # type: ignore

DEFAULT_SETTINGS: Dict[str, Any] = {
    "concurrency": 5,
    "requestTimeout": 15,
    "userAgent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "includeMenuByDefault": True,
    "verifySsl": True,
    "logLevel": "INFO",
    "output": {
        "directory": "data",
        "filename": "restaurants_output.json",
    },
    "retry": {
        "maxAttempts": 3,
        "backoffSeconds": 2,
    },
}

def load_settings(path: Optional[Path]) -> Dict[str, Any]:
    if path is None or not path.exists():
        return DEFAULT_SETTINGS.copy()

    try:
        with path.open("r", encoding="utf-8") as f:
            user_settings = json.load(f)
        merged = DEFAULT_SETTINGS.copy()
        # Deep-merge simple nested dicts
        for key, value in user_settings.items():
            if (
                isinstance(value, dict)
                and key in merged
                and isinstance(merged[key], dict)
            ):
                merged[key] = {**merged[key], **value}
            else:
                merged[key] = value
        return merged
    except Exception as exc:  # pragma: no cover - defensive
        print(f"Failed to read settings from {path}: {exc}", file=sys.stderr)
        return DEFAULT_SETTINGS.copy()

def configure_logging(level_name: str) -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    )

def load_input_urls(path: Path, include_menu_default: bool) -> List[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    urls: List[Dict[str, Any]] = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, str):
                urls.append({"url": item, "scrapeMenu": include_menu_default})
            elif isinstance(item, dict) and "url" in item:
                urls.append(
                    {
                        "url": item["url"],
                        "scrapeMenu": bool(
                            item.get("scrapeMenu", include_menu_default)
                        ),
                    }
                )
    else:
        raise ValueError(
            "input_urls JSON must be a list of URLs or objects with a 'url' key."
        )

    return urls

def process_single(
    parser: RestaurantParser,
    url: str,
    scrape_menu: bool,
) -> Optional[Dict[str, Any]]:
    logger = logging.getLogger("uber_eats_scraper")
    try:
        logger.info("Scraping %s (menu=%s)", url, scrape_menu)
        restaurant = parser.scrape_restaurant(url, include_menu=scrape_menu)
        if restaurant is None:
            logger.warning("No data parsed for %s", url)
        return restaurant
    except Exception as exc:  # pragma: no cover - network / parsing errors
        logger.error("Failed to scrape %s: %s", url, exc, exc_info=True)
        return None

def main(argv: Optional[List[str]] = None) -> int:
    project_root = CURRENT_DIR.parent

    parser = argparse.ArgumentParser(
        description="Uber Eats restaurant scraper"
    )
    parser.add_argument(
        "--input",
        "-i",
        type=str,
        default=str(project_root / "data" / "input_urls.sample.json"),
        help="Path to JSON file with restaurant URLs.",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default=None,
        help="Output JSON file path. Defaults to config output settings.",
    )
    parser.add_argument(
        "--config",
        "-c",
        type=str,
        default=str(project_root / "src" / "config" / "settings.example.json"),
        help="Path to settings JSON file.",
    )
    parser.add_argument(
        "--no-menu",
        action="store_true",
        help="Disable menu scraping for all URLs.",
    )

    args = parser.parse_args(argv)

    settings_path = Path(args.config) if args.config else None
    settings = load_settings(settings_path)

    configure_logging(settings.get("logLevel", "INFO"))
    logger = logging.getLogger("uber_eats_scraper")

    include_menu_default = bool(settings.get("includeMenuByDefault", True))
    if args.no_menu:
        include_menu_default = False

    input_path = Path(args.input)
    if not input_path.exists():
        logger.error("Input file does not exist: %s", input_path)
        return 1

    urls = load_input_urls(input_path, include_menu_default)

    output_path: Path
    if args.output:
        output_path = Path(args.output)
    else:
        output_cfg = settings.get("output", {})
        output_dir = output_cfg.get("directory", "data")
        output_name = output_cfg.get("filename", "restaurants_output.json")
        output_path = project_root / output_dir / output_name

    retry_cfg = settings.get("retry", {})
    parser_instance = RestaurantParser(
        user_agent=settings.get("userAgent"),
        timeout=int(settings.get("requestTimeout", 15)),
        verify_ssl=bool(settings.get("verifySsl", True)),
        max_attempts=int(retry_cfg.get("maxAttempts", 3)),
        backoff_seconds=float(retry_cfg.get("backoffSeconds", 2.0)),
    )

    results: List[Dict[str, Any]] = []
    concurrency = int(settings.get("concurrency", 5))

    logger.info(
        "Starting scrape of %d URLs with concurrency=%d", len(urls), concurrency
    )

    if concurrency <= 1:
        for item in urls:
            data = process_single(
                parser_instance,
                item["url"],
                bool(item.get("scrapeMenu", include_menu_default)),
            )
            if data:
                results.append(data)
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            future_map = {
                executor.submit(
                    process_single,
                    parser_instance,
                    item["url"],
                    bool(item.get("scrapeMenu", include_menu_default)),
                ): item["url"]
                for item in urls
            }
            for future in as_completed(future_map):
                data = future.result()
                if data:
                    results.append(data)

    if not results:
        logger.warning("No restaurant data collected. Nothing to write.")
    else:
        export_to_file(results, output_path)
        logger.info("Wrote %d restaurants to %s", len(results), output_path)

    return 0

if __name__ == "__main__":
    raise SystemExit(main())