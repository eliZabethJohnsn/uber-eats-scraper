thonfrom datetime import datetime, time
from typing import Any, Dict, List, Optional, Union

def parse_location(node: Union[Dict[str, Any], List[Any]]) -> Dict[str, Any]:
    """
    Attempts to extract a location object with:
      address, city, postalCode, country, latitude, longitude, locationType
    """
    candidate: Optional[Dict[str, Any]] = None

    def _walk(n: Any) -> None:
        nonlocal candidate
        if candidate is not None:
            return

        if isinstance(n, dict):
            if (
                "latitude" in n
                and "longitude" in n
                and isinstance(n.get("latitude"), (int, float))
                and isinstance(n.get("longitude"), (int, float))
            ):
                candidate = n
                return
            for v in n.values():
                _walk(v)
        elif isinstance(n, list):
            for item in n:
                _walk(item)

    _walk(node)

    loc = candidate or {}

    address = loc.get("address") or loc.get("streetAddress") or loc.get("line1")
    city = loc.get("city") or loc.get("town") or loc.get("locality")
    postal_code = loc.get("postalCode") or loc.get("zipCode")
    country = loc.get("country") or loc.get("countryCode")
    latitude = loc.get("latitude")
    longitude = loc.get("longitude")
    location_type = loc.get("locationType") or loc.get("type") or "DEFAULT"

    return {
        "address": address,
        "city": city,
        "postalCode": postal_code,
        "country": country,
        "latitude": latitude,
        "longitude": longitude,
        "locationType": location_type,
    }

def parse_hours(node: Union[Dict[str, Any], List[Any]]) -> List[Dict[str, Any]]:
    """
    Attempts to create a normalized 'hours' list in the form:

    [
        {
            "dayRange": "Sunday",
            "sectionHours": [
                {
                    "startTime": 240,
                    "endTime": 659,
                    "sectionTitle": "Breakfast",
                    "startTimeFormatted": "4:00 AM",
                    "endTimeFormatted": "10:59 AM"
                }
            ]
        }
    ]

    If a pre-normalized `hours` field exists, returns that unchanged.
    """
    hours_raw = _find_first_key(node, "hours")
    if isinstance(hours_raw, list):
        # Assume already normalized
        if all(isinstance(item, dict) for item in hours_raw):
            return hours_raw  # type: ignore[return-value]

    # Some APIs use openingHours or similar
    schedule = (
        _find_first_key(node, "openingHours")
        or _find_first_key(node, "schedule")
        or _find_first_key(node, "hoursOfOperation")
    )
    if not schedule:
        return []

    normalized: List[Dict[str, Any]] = []
    # Handle a dict like {"Sunday": [{"start": "...", "end": "..."}], ...}
    if isinstance(schedule, dict):
        for day, sections in schedule.items():
            if not isinstance(sections, list):
                continue
            normalized_sections = []
            for section in sections:
                if not isinstance(section, dict):
                    continue
                start_raw = section.get("start") or section.get("openTime")
                end_raw = section.get("end") or section.get("closeTime")
                title = section.get("sectionTitle") or section.get("label") or "Open"

                start_minutes, start_formatted = _parse_time_to_minutes(start_raw)
                end_minutes, end_formatted = _parse_time_to_minutes(end_raw)

                if start_minutes is None or end_minutes is None:
                    continue

                normalized_sections.append(
                    {
                        "startTime": start_minutes,
                        "endTime": end_minutes,
                        "sectionTitle": title,
                        "startTimeFormatted": start_formatted,
                        "endTimeFormatted": end_formatted,
                    }
                )

            if normalized_sections:
                normalized.append(
                    {
                        "dayRange": str(day),
                        "sectionHours": normalized_sections,
                    }
                )
        return normalized

    return []

def _parse_time_to_minutes(
    value: Optional[Union[str, int, float]],
) -> (Optional[int], Optional[str]):
    """
    Accepts "04:00", "4:00 AM", or minutes-since-midnight.
    Returns (minutes_since_midnight, human_readable).
    """
    if value is None:
        return None, None

    if isinstance(value, (int, float)):
        minutes = int(value)
        return minutes, _format_minutes(minutes)

    if not isinstance(value, str):
        return None, None

    value = value.strip()

    # HH:MM or H:MM (optionally with AM/PM)
    try:
        if "AM" in value.upper() or "PM" in value.upper():
            dt = datetime.strptime(value.upper(), "%I:%M %p")
        else:
            dt = datetime.strptime(value, "%H:%M")
        minutes = dt.hour * 60 + dt.minute
        return minutes, _format_minutes(minutes)
    except Exception:
        return None, None

def _format_minutes(minutes: int) -> str:
    h = minutes // 60
    m = minutes % 60
    t = time(hour=h % 24, minute=m)
    return t.strftime("%-I:%M %p") if hasattr(t, "strftime") else f"{h}:{m:02d}"

def _find_first_key(
    node: Union[Dict[str, Any], List[Any]],
    key: str,
) -> Any:
    def _walk(n: Any) -> Any:
        if isinstance(n, dict):
            if key in n:
                return n[key]
            for v in n.values():
                result = _walk(v)
                if result is not None:
                    return result
        elif isinstance(n, list):
            for item in n:
                result = _walk(item)
                if result is not None:
                    return result
        return None

    return _walk(node)