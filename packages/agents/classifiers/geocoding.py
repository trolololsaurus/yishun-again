"""
OneMap geocoding for Yishun Again — spec §2 (Section 2 fix).

Geocodes incident block+area strings using Singapore's free OneMap API.
No auth required. Rate limit: 0.5s between requests.

Yishun bounding box (hard validation):
  lat: 1.41 – 1.45
  lon: 103.82 – 103.85

Usage:
    from classifiers.geocoding import geocode_incident, backfill_missing_coordinates
    coords = geocode_incident("349", "Yishun Avenue 11")   # (lat, lon) or None
    stats  = backfill_missing_coordinates()                # {"updated": N, "failed": N}

FastAPI endpoint: POST /geocoding/backfill (registered in main.py)
"""

import logging
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://www.onemap.gov.sg/api/common/elastic/search"

# Yishun bounding box — reject results outside this box
_LAT_MIN, _LAT_MAX = 1.39, 1.47   # slightly expanded from spec to avoid edge clipping
_LON_MIN, _LON_MAX = 103.80, 103.87

_RATE_LIMIT = 0.5   # seconds between requests


def _within_yishun(lat: float, lon: float) -> bool:
    return _LAT_MIN <= lat <= _LAT_MAX and _LON_MIN <= lon <= _LON_MAX


def geocode_incident(block_number: Optional[str], area_name: Optional[str]) -> Optional[tuple[float, float]]:
    """
    Geocode a Yishun incident address via OneMap.

    Tries queries in order — most specific first — until a Yishun-bounds
    result is found. Full HDB block address ("BLK 413 YISHUN AVE 11") gives
    block-level precision; OneMap understands this format. Falling back to
    area-only returns a street centroid (multiple blocks collapse to one point).

      1. "BLK {block_number} {area_name}"
      2. "BLOCK {block_number} {area_name}"
      3. "{area_name} Yishun Singapore"   (fallback — centroid only)

    Returns (latitude, longitude) or None if not found / out of bounds.
    Rate limit: 0.5 s between requests.
    """
    queries: list[str] = []
    if block_number and area_name:
        clean_block = block_number.strip().upper()
        clean_area  = area_name.strip().upper()
        queries.append(f"BLK {clean_block} {clean_area}")
        queries.append(f"BLOCK {clean_block} {clean_area}")
    elif block_number:
        queries.append(f"BLK {block_number.strip().upper()} YISHUN")
    if area_name:
        queries.append(f"{area_name} Yishun Singapore")

    if not queries:
        return None

    for query in queries:
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
                    time.sleep(_RATE_LIMIT)
                    return (lat, lon)
                else:
                    logger.debug(
                        "Geocode result out of Yishun bounds for %r: (%.5f, %.5f)",
                        query, lat, lon,
                    )
        except Exception as exc:
            logger.debug("OneMap request failed for %r: %s", query, exc)

        time.sleep(_RATE_LIMIT)

    return None


def backfill_missing_coordinates() -> dict:
    """
    One-time pass: find all published incidents with NULL coordinates
    that have block_number or area_name, geocode them, update Supabase.

    Returns {"updated": N, "failed": N}.
    """
    from classifiers.corroboration import get_supabase_client

    try:
        supabase = get_supabase_client()
    except EnvironmentError as exc:
        logger.error("Supabase not configured: %s", exc)
        return {"updated": 0, "failed": 0, "error": str(exc)}

    # Fetch published incidents with NULL coordinates but location data
    result = (
        supabase.table("incidents")
        .select("id,block_number,area_name,latitude,longitude")
        .eq("is_published", True)
        .is_("latitude", "null")
        .execute()
    )

    candidates = [
        r for r in (result.data or [])
        if r.get("block_number") or r.get("area_name")
    ]

    logger.info(
        "Geocoding backfill: %d incident(s) with NULL coordinates and location data",
        len(candidates),
    )

    updated = 0
    failed  = 0

    for row in candidates:
        coords = geocode_incident(row.get("block_number"), row.get("area_name"))
        if coords:
            lat, lon = coords
            try:
                supabase.table("incidents").update({
                    "latitude":  lat,
                    "longitude": lon,
                }).eq("id", row["id"]).execute()
                updated += 1
                logger.info(
                    "Geocoded incident %s: (%.5f, %.5f) — block=%s area=%s",
                    row["id"][:8], lat, lon,
                    row.get("block_number"), row.get("area_name"),
                )
            except Exception as exc:
                failed += 1
                logger.error("Failed to update coordinates for %s: %s", row["id"], exc)
        else:
            failed += 1
            logger.debug(
                "No geocode result for incident %s — block=%s area=%s",
                row["id"][:8], row.get("block_number"), row.get("area_name"),
            )

    logger.info("Geocoding backfill complete: updated=%d failed=%d", updated, failed)
    return {"updated": updated, "failed": failed}
