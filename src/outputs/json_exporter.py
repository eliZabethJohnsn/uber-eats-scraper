thonimport json
import logging
from pathlib import Path
from typing import Any, List

logger = logging.getLogger(__name__)

def export_to_file(data: List[Any], path: Path) -> None:
    """
    Writes the list of restaurant documents to a JSON file.
    Creates parent directories if needed.
    """
    path = path.resolve()
    if not path.parent.exists():
        logger.debug("Creating output directory %s", path.parent)
        path.parent.mkdir(parents=True, exist_ok=True)

    logger.debug("Writing %d records to %s", len(data), path)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    logger.info("Exported data to %s", path)