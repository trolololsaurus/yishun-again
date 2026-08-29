import logging
import os
from datetime import date, datetime, timedelta, timezone

import httpx

logger = logging.getLogger(__name__)

# Confirmed by live GraphQL introspection against Cloudflare's API
# (2026-08-26) — do not guess field names here, re-introspect if this breaks:
#   query { __type(name: "AccountHttpRequestsAdaptiveGroupsDimensions") { fields { name } } }
# This is EDGE/CDN request data (httpRequestsAdaptiveGroups), not the RUM
# browser beacon — it counts every request Cloudflare's edge saw for the zone,
# bots included. `sum.visits` is Cloudflare's own visit-session metric
# (requests from one client without a 30min gap collapse into one visit).
# Bot vs. human split (botManagementDecision / botScore) and true unique
# visitors are NOT available — confirmed live, both 403 ("does not have
# access to the field") on this zone's plan. Do not add UI implying either.
CF_GRAPHQL_URL = "https://api.cloudflare.com/client/v4/graphql"

# Free-plan zones cap httpRequestsAdaptiveGroups at a 1-day TOTAL time span
# per query, regardless of bucket granularity — confirmed live: a 7-day
# hourly-or-daily-grouped single query is rejected with a "quota" error, but
# an exact 24h span (even grouped hourly, 24 points) succeeds in one request.
# So: the "24h" window is ONE query with hourly buckets; "7d" still needs one
# query per calendar day, same as before this window param existed.
_HOUR_QUERY = """
query($zoneTag: string, $since: Time, $until: Time) {
  viewer {
    zones(filter: {zoneTag: $zoneTag}) {
      byHour: httpRequestsAdaptiveGroups(limit: 30, filter: {datetime_geq: $since, datetime_lt: $until}, orderBy: [datetimeHour_ASC]) {
        sum { visits }
        count
        dimensions { datetimeHour }
      }
      byCountry: httpRequestsAdaptiveGroups(limit: 10, filter: {datetime_geq: $since, datetime_lt: $until}, orderBy: [sum_visits_DESC]) {
        sum { visits }
        dimensions { clientCountryName }
      }
      byDevice: httpRequestsAdaptiveGroups(limit: 10, filter: {datetime_geq: $since, datetime_lt: $until}, orderBy: [sum_visits_DESC]) {
        sum { visits }
        dimensions { clientDeviceType }
      }
    }
  }
}
"""

_HOUR_REFERER_QUERY = """
query($zoneTag: string, $since: Time, $until: Time) {
  viewer {
    zones(filter: {zoneTag: $zoneTag}) {
      byReferer: httpRequestsAdaptiveGroups(limit: 10, filter: {datetime_geq: $since, datetime_lt: $until}, orderBy: [sum_visits_DESC]) {
        sum { visits }
        dimensions { clientRefererHost }
      }
    }
  }
}
"""

# clientRefererHost is its OWN query, deliberately separate from the rest.
# Confirmed live against this zone: that field alone 403s on this plan, and
# Cloudflare fails the ENTIRE query — including unrelated aliases in the same
# request — when any one aliased field errors. Isolating it means a
# referrer-access restriction costs us referrers only, not the whole window.
_DAY_QUERY = """
query($zoneTag: string, $day: Date) {
  viewer {
    zones(filter: {zoneTag: $zoneTag}) {
      byCountry: httpRequestsAdaptiveGroups(limit: 10, filter: {date: $day}, orderBy: [sum_visits_DESC]) {
        sum { visits }
        dimensions { clientCountryName }
      }
      byDevice: httpRequestsAdaptiveGroups(limit: 10, filter: {date: $day}, orderBy: [sum_visits_DESC]) {
        sum { visits }
        dimensions { clientDeviceType }
      }
      total: httpRequestsAdaptiveGroups(limit: 1, filter: {date: $day}) {
        sum { visits }
        count
      }
    }
  }
}
"""

_DAY_REFERER_QUERY = """
query($zoneTag: string, $day: Date) {
  viewer {
    zones(filter: {zoneTag: $zoneTag}) {
      byReferer: httpRequestsAdaptiveGroups(limit: 10, filter: {date: $day}, orderBy: [sum_visits_DESC]) {
        sum { visits }
        dimensions { clientRefererHost }
      }
    }
  }
}
"""

# "30d" is deliberately absent. Confirmed live (2026-08-27): this dataset's
# retention on this zone's plan caps at "1w1d" (8 days) TOTAL — a query for
# anything older is rejected outright, separate from and in addition to the
# 1-day-per-query span cap above. A 30-day option would silently return only
# the ~7 retrievable days, mislabeled as 30, while burning ~23 always-failing
# requests every load. If a paid tier ever changes this, re-verify live
# before re-adding — don't assume a longer retention window exists.
WINDOWS = {"24h": None, "7d": 7}  # days is None for 24h (hourly, not daily)


def _run_query(client: httpx.Client, query: str, variables: dict, label: str) -> dict:
    resp = client.post(CF_GRAPHQL_URL, json={"query": query, "variables": variables}, timeout=15)
    resp.raise_for_status()
    body = resp.json()
    if body.get("errors"):
        raise RuntimeError(f"Cloudflare GraphQL error for {label}: {body['errors']}")
    zones = body["data"]["viewer"]["zones"]
    if not zones:
        raise RuntimeError(f"Zone not visible to this token ({label})")
    return zones[0]


def _tally(rows: list[dict], dim_key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        key = row["dimensions"].get(dim_key) or "unknown"
        out[key] = out.get(key, 0) + row["sum"]["visits"]
    return out


def _top10(tally: dict[str, int], key_name: str) -> list[dict]:
    return [
        {key_name: k, "visits": v}
        for k, v in sorted(tally.items(), key=lambda kv: -kv[1])[:10]
    ]


def _record_failure(errors: list[str], label: str, exc: Exception) -> None:
    """Log + append one failed-query error. `errors.append(...)` alone left
    nothing for the UI's "see server logs" to find."""
    logger.warning("cf_analytics: %s query failed: %s", label, exc)
    errors.append(f"{label}: {exc}")


def _get_24h(client: httpx.Client, zone_tag: str) -> dict:
    until = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    since = until - timedelta(hours=24)
    variables = {"zoneTag": zone_tag, "since": since.isoformat(), "until": until.isoformat()}

    errors: list[str] = []
    points: list[dict] = []
    countries: dict[str, int] = {}
    devices: dict[str, int] = {}
    referrers: dict[str, int] = {}

    try:
        zone = _run_query(client, _HOUR_QUERY, variables, "24h")
        points = [
            {"t": row["dimensions"]["datetimeHour"], "visits": row["sum"]["visits"], "requests": row["count"]}
            for row in zone["byHour"]
        ]
        countries = _tally(zone["byCountry"], "clientCountryName")
        devices = _tally(zone["byDevice"], "clientDeviceType")
    except Exception as exc:
        _record_failure(errors, "24h", exc)

    try:
        referer_zone = _run_query(client, _HOUR_REFERER_QUERY, variables, "24h referrers")
        referrers = _tally(referer_zone["byReferer"], "clientRefererHost")
    except Exception as exc:
        _record_failure(errors, "24h (referrers)", exc)

    return {"points": points, "countries": countries, "devices": devices, "referrers": referrers, "errors": errors}


def _get_multi_day(client: httpx.Client, zone_tag: str, days: int) -> dict:
    points: list[dict] = []
    countries: dict[str, int] = {}
    devices: dict[str, int] = {}
    referrers: dict[str, int] = {}
    errors: list[str] = []

    for i in range(1, days + 1):
        day = date.today() - timedelta(days=i)
        variables = {"zoneTag": zone_tag, "day": day.isoformat()}
        try:
            zone = _run_query(client, _DAY_QUERY, variables, day.isoformat())
        except Exception as exc:
            _record_failure(errors, day.isoformat(), exc)
            continue

        total = zone["total"][0] if zone["total"] else {"sum": {"visits": 0}, "count": 0}
        points.append({
            "t": f"{day.isoformat()}T00:00:00Z",
            "visits": total["sum"]["visits"],
            "requests": total["count"],
        })
        for k, v in _tally(zone["byCountry"], "clientCountryName").items():
            countries[k] = countries.get(k, 0) + v
        for k, v in _tally(zone["byDevice"], "clientDeviceType").items():
            devices[k] = devices.get(k, 0) + v

        try:
            referer_zone = _run_query(client, _DAY_REFERER_QUERY, variables, f"{day.isoformat()} referrers")
            for k, v in _tally(referer_zone["byReferer"], "clientRefererHost").items():
                referrers[k] = referrers.get(k, 0) + v
        except Exception as exc:
            _record_failure(errors, f"{day.isoformat()} (referrers)", exc)

    points.sort(key=lambda p: p["t"])
    return {"points": points, "countries": countries, "devices": devices, "referrers": referrers, "errors": errors}


def get_traffic_summary(window: str = "7d") -> dict:
    """Zone-level Cloudflare traffic for the given window.

    window: "24h" (hourly buckets, one request) or "7d" (daily buckets, one
    request per day — free-plan quota caps a single query at a 1-day span
    regardless of grouping granularity). "30d" is not supported — see WINDOWS.

    Returns:
        {
          "window": "24h" | "7d",
          "granularity": "hour" | "day",
          "points": [{"t": ISO8601, "visits": int, "requests": int}, ...],
          "countries": [{"country": "SG", "visits": int}, ...],   # top 10
          "referrers": [{"host": "t.co", "visits": int}, ...],    # top 10
          "devices":   [{"device": "desktop", "visits": int}, ...],
          "total_visits": int,
          "total_requests": int,
          "errors": [str, ...],
        }
    """
    if window not in WINDOWS:
        raise ValueError(f"window must be one of {list(WINDOWS)}, got {window!r}")

    # .strip(): --env-vars-file passes values through literally, unlike
    # python-dotenv (used locally) which trims trailing whitespace on load —
    # a stray trailing space in the deploy file silently broke every query
    # ("Zone not visible to this token") while local dev looked fine.
    zone_tag = os.environ["CF_ZONE_ID"].strip()
    token = os.environ["CF_ANALYTICS_API_TOKEN"].strip()

    with httpx.Client(headers={"Authorization": f"Bearer {token}"}) as client:
        result = _get_24h(client, zone_tag) if window == "24h" else _get_multi_day(client, zone_tag, WINDOWS[window])

    return {
        "window": window,
        "granularity": "hour" if window == "24h" else "day",
        "points": result["points"],
        "countries": _top10(result["countries"], "country"),
        "referrers": _top10(result["referrers"], "host"),
        "devices": _top10(result["devices"], "device"),
        "total_visits": sum(p["visits"] for p in result["points"]),
        "total_requests": sum(p["requests"] for p in result["points"]),
        "errors": result["errors"],
    }
