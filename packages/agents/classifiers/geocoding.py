"""
OneMap geocoding for Yishun Again — spec §2 (Section 2 fix).

Geocodes incident location strings using Singapore's free OneMap API.
No auth required. Rate limit: 0.5s between requests.

Priority order (most precise first — June-2026 pin overhaul):
  1. HDB block + street        → "BLK 349 YISHUN AVENUE 11"
  2. Full address in block field (e.g. "5 Yishun Street 23") → raw query
  3. Prominent place / POI     → whitelist match (hospital, mall, MRT, park…)
  4. Street name alone         → street centroid
  5. Nothing usable            → None (NO pin — never the generic Yishun centre)

LLM-estimated coordinates are never trusted; this module is the only source
of published pin coordinates.

Yishun bounding box (hard validation):
  lat: 1.39 – 1.47
  lon: 103.80 – 103.87

Usage:
    from classifiers.geocoding import geocode_incident, backfill_all_coordinates
    coords = geocode_incident("349", "Yishun Avenue 11")          # (lat, lon) | None
    coords = geocode_incident(None, "Yishun", extra_text=title)   # POI scan on title
    stats  = backfill_all_coordinates()   # re-geocode EVERY published incident

FastAPI endpoint: POST /geocoding/backfill (registered in main.py)
"""

import logging
import re
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://www.onemap.gov.sg/api/common/elastic/search"

# Yishun bounding box — reject results outside this box
_LAT_MIN, _LAT_MAX = 1.39, 1.47   # slightly expanded from spec to avoid edge clipping
_LON_MIN, _LON_MAX = 103.80, 103.87

_RATE_LIMIT = 0.5   # seconds between requests

# A real HDB block number: 1-4 digits + optional letter suffix ("349", "512C").
# "NS13", "North Gaia", "Yishun Avenue 6" must NOT match.
_BLOCK_RE = re.compile(r"^\s*(?:BLK\.?|BLOCK)?\s*(\d{1,4}[A-Z]?)\s*$", re.IGNORECASE)

# Street-like phrase anywhere in a string. Covers Yishun's street grammar plus
# the boundary roads that appear in the data.
_STREET_RE = re.compile(
    r"\b("
    r"YISHUN\s+(?:AVENUE|AVE|STREET|ST|RING\s+ROAD|CENTRAL|CLOSE|DRIVE|DR|LINK"
    r"|GROVE|WALK|PLACE|CRESCENT|LOOP|LANE)(?:\s+\d+)?"
    r"|LENTOR\s+AVENUE"
    r"|SEMBAWANG\s+ROAD"
    r"|MILTONIA\s+CLOSE"
    r"|CANBERRA\s+(?:ROAD|LINK|DRIVE)"
    r")\b",
    re.IGNORECASE,
)

# Prominent-place whitelist: (match substring, OneMap query). Scanned in order —
# put more specific aliases before broader ones ("yishun park hawker" before
# "yishun park"). Matching is case-insensitive substring over block_number,
# area_name and title ONLY (never the summary: every dagger story mentions
# "taken to Khoo Teck Puat Hospital", which would mis-pin them at the hospital).
_POI_ALIASES: list[tuple[str, str]] = [
    ("khoo teck puat",                  "KHOO TECK PUAT HOSPITAL"),
    ("yishun community hospital",       "YISHUN COMMUNITY HOSPITAL"),
    ("yishun polyclinic",               "YISHUN POLYCLINIC"),
    ("northpoint",                      "NORTHPOINT CITY"),
    ("yishun integrated transport hub", "YISHUN INTEGRATED TRANSPORT HUB"),
    ("yishun bus interchange",          "YISHUN INTEGRATED TRANSPORT HUB"),
    ("yishun interchange",              "YISHUN INTEGRATED TRANSPORT HUB"),
    ("yishun mrt",                      "YISHUN MRT STATION"),
    ("safra yishun",                    "SAFRA YISHUN"),
    ("yishun park hawker",              "YISHUN PARK HAWKER CENTRE"),
    ("yishun park connector",           "YISHUN PARK"),
    ("yishun park",                     "YISHUN PARK"),
    ("yishun pond",                     "YISHUN POND"),
    ("yishun stadium",                  "YISHUN STADIUM"),
    ("yishun swimming",                 "YISHUN SWIMMING COMPLEX"),
    ("yishun sports hall",              "YISHUN SPORTS HALL"),
    ("yishun public library",           "YISHUN PUBLIC LIBRARY"),
    ("yishun library",                  "YISHUN PUBLIC LIBRARY"),
    ("chong pang",                      "CHONG PANG MARKET AND FOOD CENTRE"),
    ("wisteria",                        "WISTERIA MALL"),
    ("junction nine",                   "JUNCTION NINE"),
    ("junction 9",                      "JUNCTION NINE"),
    ("north gaia",                      "NORTH GAIA"),
    ("yishun 10",                       "YISHUN 10"),
    ("yishun ten",                      "YISHUN 10"),
    ("gv yishun",                       "YISHUN 10"),
    ("north view primary",              "NORTH VIEW PRIMARY SCHOOL"),
    ("chung cheng high",                "CHUNG CHENG HIGH SCHOOL YISHUN"),
    ("yishun industrial park",          "YISHUN INDUSTRIAL PARK A"),
    ("orchid country club",             "ORCHID COUNTRY CLUB"),
    ("yishun dam",                      "YISHUN DAM"),
    ("lower seletar",                   "LOWER SELETAR RESERVOIR PARK"),
]


def _within_yishun(lat: float, lon: float) -> bool:
    return _LAT_MIN <= lat <= _LAT_MAX and _LON_MIN <= lon <= _LON_MAX


def _clean_block(block_number: Optional[str]) -> Optional[str]:
    """Return the bare block number ("349", "512C") or None if the field
    holds something else (street name, POI, MRT code…)."""
    if not block_number:
        return None
    m = _BLOCK_RE.match(block_number)
    return m.group(1).upper() if m else None


def _find_street(*texts: Optional[str]) -> Optional[str]:
    """First street-like phrase found across the given strings."""
    for text in texts:
        if not text:
            continue
        m = _STREET_RE.search(text)
        if m:
            return re.sub(r"\s+", " ", m.group(1).upper().strip())
    return None


def _find_poi(*texts: Optional[str]) -> Optional[str]:
    """First POI-whitelist match across the given strings → OneMap query."""
    combined = " ".join(t for t in texts if t).lower()
    if not combined:
        return None
    for alias, query in _POI_ALIASES:
        if alias in combined:
            return query
    return None


def _abbrev_street(street: str) -> str:
    """OneMap address records use abbreviated street types ("YISHUN AVE 11")."""
    s = street.upper()
    s = re.sub(r"\bAVENUE\b", "AVE", s)
    s = re.sub(r"\bSTREET\b", "ST", s)
    s = re.sub(r"\bRING\s+ROAD\b", "RING RD", s)
    s = re.sub(r"\bDRIVE\b", "DR", s)
    return s


def _onemap_lookup(query: str) -> Optional[tuple[float, float]]:
    """Single OneMap query → (lat, lon) if the top hit is inside Yishun."""
    try:
        resp = httpx.get(
            BASE_URL,
            params={
                "searchVal":      query,
                "returnGeom":     "Y",
                "getAddrDetails": "Y",
                "pageNum":        "1",
            },
            timeout=8.0,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        if results:
            lat = float(results[0]["LATITUDE"])
            lon = float(results[0]["LONGITUDE"])
            if _within_yishun(lat, lon):
                logger.debug("Geocoded %r → (%.5f, %.5f)", query, lat, lon)
                return (lat, lon)
            logger.debug("Out of Yishun bounds for %r: (%.5f, %.5f)", query, lat, lon)
    except Exception as exc:
        logger.debug("OneMap request failed for %r: %s", query, exc)
    return None


def build_geocode_queries(
    block_number: Optional[str],
    area_name: Optional[str],
    extra_text: Optional[str] = None,
) -> list[tuple[str, str]]:
    """
    Assemble (method, query) pairs in priority order:
    block → full-address → poi → street. Empty list = nothing usable (no pin).
    """
    queries: list[tuple[str, str]] = []

    block  = _clean_block(block_number)
    street = _find_street(area_name, block_number)
    poi    = _find_poi(block_number, area_name, extra_text)

    # 1. Block-level — the gold standard. OneMap's address index matches the
    # abbreviated postal form ("349 YISHUN AVE 11") most reliably.
    if block and street:
        ab = _abbrev_street(street)
        queries.append(("block", f"{block} {ab}"))
        if ab != street:
            queries.append(("block", f"{block} {street}"))
        queries.append(("block", f"BLK {block} {ab}"))
    elif block:
        # Block with no street — OneMap can often resolve "323 YISHUN"
        queries.append(("block", f"{block} YISHUN"))
        queries.append(("block", f"BLK {block} YISHUN"))

    # 2. block_number field holding a full address ("5 Yishun Street 23")
    if not block and block_number and re.search(r"\d", block_number) and _STREET_RE.search(block_number):
        raw = re.sub(r"\s+", " ", block_number.strip().upper())
        queries.append(("address", _abbrev_street(raw)))
        if _abbrev_street(raw) != raw:
            queries.append(("address", raw))

    # 3. Prominent place
    if poi:
        queries.append(("poi", poi))

    # 4. Street centroid — last resort with a real location, better than nothing
    if street:
        queries.append(("street", street))

    return queries


def geocode_incident(
    block_number: Optional[str],
    area_name: Optional[str],
    extra_text: Optional[str] = None,
) -> Optional[tuple[float, float]]:
    """
    Geocode a Yishun incident via OneMap using the priority order above.

    extra_text (usually the incident title) is scanned for POI mentions only.
    Returns (latitude, longitude) or None when no query resolves inside the
    Yishun bounding box. A bare "Yishun" with no block/POI/street returns
    None — such incidents get NO map pin rather than stacking at the centre.
    Rate limit: 0.5 s between requests.
    """
    for _method, query in build_geocode_queries(block_number, area_name, extra_text):
        coords = _onemap_lookup(query)
        time.sleep(_RATE_LIMIT)
        if coords:
            return coords
    return None


def geocode_incident_with_method(
    block_number: Optional[str],
    area_name: Optional[str],
    extra_text: Optional[str] = None,
) -> tuple[Optional[tuple[float, float]], Optional[str]]:
    """Like geocode_incident, but also reports which method resolved."""
    for method, query in build_geocode_queries(block_number, area_name, extra_text):
        coords = _onemap_lookup(query)
        time.sleep(_RATE_LIMIT)
        if coords:
            return coords, method
    return None, None


def backfill_missing_coordinates() -> dict:
    """
    Incremental pass: geocode published incidents with NULL coordinates.
    Returns {"updated": N, "failed": N}.
    """
    return _run_backfill(only_missing=True)


def backfill_all_coordinates() -> dict:
    """
    Full re-geocode of EVERY published incident (June-2026 pin overhaul).
    Overwrites LLM-guessed centre-stamped coordinates; incidents with no
    usable location get NULL coordinates (no pin).

    Returns {"updated": N, "nulled": N, "failed": N, "by_method": {...}}.
    """
    return _run_backfill(only_missing=False)


def _run_backfill(only_missing: bool) -> dict:
    from classifiers.corroboration import get_supabase_client

    try:
        supabase = get_supabase_client()
    except EnvironmentError as exc:
        logger.error("Supabase not configured: %s", exc)
        return {"updated": 0, "nulled": 0, "failed": 0, "error": str(exc)}

    q = (
        supabase.table("incidents")
        .select("id,slug,title,block_number,area_name,latitude,longitude")
        .eq("is_published", True)
    )
    if only_missing:
        q = q.is_("latitude", "null")
    rows = q.execute().data or []

    logger.info("Geocoding backfill (%s): %d incident(s)",
                "missing-only" if only_missing else "ALL", len(rows))

    updated, nulled, failed = 0, 0, 0
    by_method: dict[str, int] = {}

    for row in rows:
        coords, method = geocode_incident_with_method(
            row.get("block_number"), row.get("area_name"), extra_text=row.get("title"),
        )
        new_lat, new_lon = coords if coords else (None, None)
        try:
            supabase.table("incidents").update({
                "latitude":  new_lat,
                "longitude": new_lon,
            }).eq("id", row["id"]).execute()
            if coords:
                updated += 1
                by_method[method or "?"] = by_method.get(method or "?", 0) + 1
                logger.info("Geocoded %s via %s: (%.5f, %.5f)",
                            row["slug"][:50], method, new_lat, new_lon)
            else:
                nulled += 1
                logger.info("No location for %s — pin removed", row["slug"][:50])
        except Exception as exc:
            failed += 1
            logger.error("Coordinate update failed for %s: %s", row["id"], exc)

    logger.info("Backfill complete: updated=%d nulled=%d failed=%d by_method=%s",
                updated, nulled, failed, by_method)
    return {"updated": updated, "nulled": nulled, "failed": failed, "by_method": by_method}
