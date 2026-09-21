import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
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

# Retention on this zone: re-verify live before trusting this number, it has
# already moved once. Confirmed live 2026-08-27 at "1w1d" (8 days) TOTAL —
# anything older rejected outright. Re-confirmed live 2026-09-21: now "4w3d"
# (31 days) — the exact boundary probed day-by-day, 30 days back succeeds, 31
# fails with `code: "quota"`, `"cannot request data older than 4w3d"`. This is
# a ROLLING window measured from request time, not a fixed historical cutoff,
# so a 30-day option stays valid indefinitely without drifting stale the way a
# hardcoded date would — it always asks for "yesterday back N days" relative
# to whenever the request runs, which is by definition inside the retained
# span. `_get_multi_day` still loops one query per day (the 1-day span-per-
# query cap below is unrelated to retention depth and still applies) — it now
# runs that loop concurrently rather than sequentially, see its own comment.
WINDOWS = {"24h": None, "7d": 7, "30d": 30}  # days is None for 24h (hourly, not daily)


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


# Verified live 2026-09-21: 20 concurrent requests against this zone/token
# completed in ~2s with zero rate-limiting (all 200 OK). httpx.Client's
# connection pool is documented thread-safe, so the per-day queries below
# fire through the SAME client from a small thread pool instead of one
# request at a time — a sequential 30d window (60 serial requests: main +
# referrer x 30 days) measured 16s end to end, which was the entire cause of
# the "why is there lag on 24h/7d/30d" complaint. Kept below the tested 20 for
# margin, not at it.
_MAX_CONCURRENT_REQUESTS = 15


def _fetch_day(client: httpx.Client, zone_tag: str, day: date, want_referrer: bool) -> dict:
    """One day's worth of data (+ referrers if want_referrer). Never raises —
    a failed day is an error entry and a missing point, not a crashed batch."""
    variables = {"zoneTag": zone_tag, "day": day.isoformat()}
    point = None
    countries: dict[str, int] = {}
    devices: dict[str, int] = {}
    referrers: dict[str, int] = {}
    errors: list[str] = []

    try:
        zone = _run_query(client, _DAY_QUERY, variables, day.isoformat())
        total = zone["total"][0] if zone["total"] else {"sum": {"visits": 0}, "count": 0}
        point = {"t": f"{day.isoformat()}T00:00:00Z",
                 "visits": total["sum"]["visits"], "requests": total["count"]}
        countries = _tally(zone["byCountry"], "clientCountryName")
        devices = _tally(zone["byDevice"], "clientDeviceType")
    except Exception as exc:
        _record_failure(errors, day.isoformat(), exc)

    if want_referrer:
        try:
            referer_zone = _run_query(client, _DAY_REFERER_QUERY, variables, f"{day.isoformat()} referrers")
            referrers = _tally(referer_zone["byReferer"], "clientRefererHost")
        except Exception as exc:
            _record_failure(errors, f"{day.isoformat()} (referrers)", exc)

    return {"point": point, "countries": countries, "devices": devices,
            "referrers": referrers, "errors": errors}


def _get_multi_day(client: httpx.Client, zone_tag: str, days: int) -> dict:
    points: list[dict] = []
    countries: dict[str, int] = {}
    devices: dict[str, int] = {}
    referrers: dict[str, int] = {}
    errors: list[str] = []

    day_list = [date.today() - timedelta(days=i) for i in range(1, days + 1)]

    # Referrer access is a property of the TOKEN/ZONE, not the day — every
    # day tested since 7d existed has 403'd on this token (see the module
    # docstring). A single probe before the parallel batch below decides
    # whether to ask for referrers AT ALL, instead of every day separately
    # rediscovering the same always-true fact — up to `days - 1` requests
    # that were guaranteed to fail, now zero.
    referrers_available = True
    probe_day = day_list[0]
    try:
        _run_query(client, _DAY_REFERER_QUERY, {"zoneTag": zone_tag, "day": probe_day.isoformat()},
                   f"{probe_day.isoformat()} referrers (probe)")
    except Exception as exc:
        referrers_available = False
        _record_failure(errors, "referrers (probed once — unavailable on "
                        "this token, skipped for the rest of the window)", exc)

    with ThreadPoolExecutor(max_workers=_MAX_CONCURRENT_REQUESTS) as pool:
        futures = [pool.submit(_fetch_day, client, zone_tag, day, referrers_available)
                   for day in day_list]
        for future in as_completed(futures):
            result = future.result()
            if result["point"]:
                points.append(result["point"])
            for k, v in result["countries"].items():
                countries[k] = countries.get(k, 0) + v
            for k, v in result["devices"].items():
                devices[k] = devices.get(k, 0) + v
            for k, v in result["referrers"].items():
                referrers[k] = referrers.get(k, 0) + v
            errors.extend(result["errors"])

    points.sort(key=lambda p: p["t"])
    return {"points": points, "countries": countries, "devices": devices, "referrers": referrers, "errors": errors}


def get_traffic_summary(window: str = "7d") -> dict:
    """Zone-level Cloudflare traffic for the given window.

    window: "24h" (hourly buckets, one request) or "7d"/"30d" (daily buckets,
    one request per day — free-plan quota caps a single query at a 1-day span
    regardless of grouping granularity, so 30d costs 30 sequential requests).
    See WINDOWS for the retention ceiling this depends on.

    Returns:
        {
          "window": "24h" | "7d" | "30d",
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
