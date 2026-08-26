import os
from datetime import date, timedelta

import httpx

# Confirmed by live GraphQL introspection against Cloudflare's API
# (2026-08-26) — do not guess field names here, re-introspect if this breaks:
#   query { __type(name: "AccountHttpRequestsAdaptiveGroupsDimensions") { fields { name } } }
# This is EDGE/CDN request data (httpRequestsAdaptiveGroups), not the RUM
# browser beacon — it counts every request Cloudflare's edge saw for the zone,
# bots included. `sum.visits` is Cloudflare's own visit-session metric
# (requests from one client without a 30min gap collapse into one visit).
CF_GRAPHQL_URL = "https://api.cloudflare.com/client/v4/graphql"

# Free-plan zones cap httpRequestsAdaptiveGroups at a 1-day window per query
# (confirmed live: a 1-day-14h span was rejected with a "quota" error) — so a
# multi-day summary means one request per day, not one wide-range request.
#
# clientRefererHost is its OWN query, deliberately separate from the rest.
# Confirmed live against this zone: that field alone 403s ("does not have
# access to the field") on whatever plan this zone is on, and Cloudflare fails
# the ENTIRE query — including the unrelated country/device/total aliases in
# the same request — when any one aliased field errors. Keeping it isolated
# means a referrer-access restriction costs us referrers, not the whole day.
_MAIN_QUERY = """
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

_REFERER_QUERY = """
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


def _run_query(client: httpx.Client, query: str, zone_tag: str, day: date) -> dict:
    resp = client.post(
        CF_GRAPHQL_URL,
        json={"query": query, "variables": {"zoneTag": zone_tag, "day": day.isoformat()}},
        timeout=15,
    )
    resp.raise_for_status()
    body = resp.json()
    if body.get("errors"):
        raise RuntimeError(f"Cloudflare GraphQL error for {day}: {body['errors']}")
    zones = body["data"]["viewer"]["zones"]
    if not zones:
        raise RuntimeError(f"Zone {zone_tag} not visible to this token")
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


def get_traffic_summary(days: int = 7) -> dict:
    """Zone-level Cloudflare traffic for the last `days` days (excludes today,
    which Cloudflare hasn't finished aggregating yet).

    Returns:
        {
          "days": [{"date": "2026-08-25", "visits": int, "requests": int}, ...],
          "countries": [{"country": "SG", "visits": int}, ...],   # top 10, summed across days
          "referrers": [{"host": "t.co", "visits": int}, ...],    # top 10, summed
          "devices":   [{"device": "desktop", "visits": int}, ...],
          "total_visits": int,
          "total_requests": int,
          "errors": [str, ...],   # per-day fetch failures, non-fatal
        }
    """
    zone_tag = os.environ["CF_ZONE_ID"]
    token = os.environ["CF_ANALYTICS_API_TOKEN"]

    daily: list[dict] = []
    countries: dict[str, int] = {}
    referrers: dict[str, int] = {}
    devices: dict[str, int] = {}
    errors: list[str] = []

    with httpx.Client(headers={"Authorization": f"Bearer {token}"}) as client:
        for i in range(1, days + 1):
            day = date.today() - timedelta(days=i)
            try:
                zone = _run_query(client, _MAIN_QUERY, zone_tag, day)
            except Exception as exc:
                errors.append(f"{day.isoformat()}: {exc}")
                continue

            total = zone["total"][0] if zone["total"] else {"sum": {"visits": 0}, "count": 0}
            daily.append({
                "date": day.isoformat(),
                "visits": total["sum"]["visits"],
                "requests": total["count"],
            })

            for k, v in _tally(zone["byCountry"], "clientCountryName").items():
                countries[k] = countries.get(k, 0) + v
            for k, v in _tally(zone["byDevice"], "clientDeviceType").items():
                devices[k] = devices.get(k, 0) + v

            try:
                referer_zone = _run_query(client, _REFERER_QUERY, zone_tag, day)
                for k, v in _tally(referer_zone["byReferer"], "clientRefererHost").items():
                    referrers[k] = referrers.get(k, 0) + v
            except Exception as exc:
                errors.append(f"{day.isoformat()} (referrers): {exc}")

    daily.sort(key=lambda d: d["date"])

    return {
        "days": daily,
        "countries": _top10(countries, "country"),
        "referrers": _top10(referrers, "host"),
        "devices": _top10(devices, "device"),
        "total_visits": sum(d["visits"] for d in daily),
        "total_requests": sum(d["requests"] for d in daily),
        "errors": errors,
    }
