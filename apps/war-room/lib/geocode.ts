// OneMap geocoding for publish-time pin creation (June-2026 pin overhaul).
// TypeScript port of packages/agents/classifiers/geocoding.py — keep the two
// in sync. Coordinates come ONLY from here; LLM-estimated values are never
// trusted. Priority: block → full address → POI → street. No match → null
// (the incident gets no map pin rather than stacking at the Yishun centre).

const BASE_URL = 'https://www.onemap.gov.sg/api/common/elastic/search'

// Yishun bounding box — reject results outside
const LAT_MIN = 1.39, LAT_MAX = 1.47
const LON_MIN = 103.80, LON_MAX = 103.87

// A real HDB block number ("349", "512C") — not streets, POIs or MRT codes
const BLOCK_RE = /^\s*(?:BLK\.?|BLOCK)?\s*(\d{1,4}[A-Z]?)\s*$/i

const STREET_RE = new RegExp(
  '\\b(' +
  'YISHUN\\s+(?:AVENUE|AVE|STREET|ST|RING\\s+ROAD|CENTRAL|CLOSE|DRIVE|DR|LINK' +
  '|GROVE|WALK|PLACE|CRESCENT|LOOP|LANE)(?:\\s+\\d+)?' +
  '|LENTOR\\s+AVENUE' +
  '|SEMBAWANG\\s+ROAD' +
  '|MILTONIA\\s+CLOSE' +
  '|CANBERRA\\s+(?:ROAD|LINK|DRIVE)' +
  ')\\b', 'i'
)

// Prominent places — scanned in order over block_number, area_name, title
// ONLY (never the summary: dagger stories routinely mention the hospital
// victims were taken to, which would mis-pin them there).
const POI_ALIASES: Array<[string, string]> = [
  ['khoo teck puat',                      'KHOO TECK PUAT HOSPITAL'],
  ['yishun community hospital',           'YISHUN COMMUNITY HOSPITAL'],
  ['yishun polyclinic',                   'YISHUN POLYCLINIC'],
  ['northpoint',                          'NORTHPOINT CITY'],
  ['yishun integrated transport hub',     'YISHUN BUS INTERCHANGE'],
  ['yishun bus interchange',              'YISHUN BUS INTERCHANGE'],
  ['yishun interchange',                  'YISHUN BUS INTERCHANGE'],
  ['bus interchange',                     'YISHUN BUS INTERCHANGE'],
  ['yishun mrt',                          'YISHUN MRT STATION'],
  ['safra yishun',                        'SAFRA YISHUN'],
  ['yishun safra',                        'SAFRA YISHUN'],
  ['yishun park hawker',                  'YISHUN HAWKER CENTRE'],
  ['yishun park connector',               'YISHUN PARK'],
  ['yishun park',                         'YISHUN PARK'],
  ['yishun boardwalk',                    'YISHUN BOARDWALK'],
  ['yishun pond',                         'YISHUN POND'],
  ['yishun stadium',                      'YISHUN STADIUM SINGAPORE'],
  ['yishun swimming',                     'YISHUN SWIMMING COMPLEX'],
  ['yishun sports hall',                  'YISHUN SPORTS HALL'],
  ['yishun public library',               'YISHUN LIBRARY'],
  ['yishun library',                      'YISHUN LIBRARY'],
  ['chong pang city',                     'CHONG PANG CITY'],
  ['chong pang',                          'CHONG PANG MARKET'],
  ['wisteria',                            'WISTERIA MALL'],
  ['junction nine',                       'JUNCTION 9'],
  ['junction 9',                          'JUNCTION 9'],
  ['north gaia',                          'NORTH GAIA'],
  ['yishun 10',                           'YISHUN 10'],
  ['yishun ten',                          'YISHUN 10'],
  ['gv yishun',                           'GV YISHUN'],
  ['north view primary',                  'NORTH VIEW PRIMARY'],
  ['chung cheng high',                    'CHUNG CHENG HIGH SCHOOL YISHUN'],
  ['yishun industrial park',              'YISHUN INDUSTRIAL PARK A'],
  ['orchid country club',                 'ORCHID COUNTRY CLUB SINGAPORE'],
  ['yishun dam',                          'YISHUN DAM'],
  ['lower seletar',                       'LOWER SELETAR RESERVOIR PARK'],
]

const withinYishun = (lat: number, lon: number) =>
  lat >= LAT_MIN && lat <= LAT_MAX && lon >= LON_MIN && lon <= LON_MAX

const cleanBlock = (blockNumber?: string | null): string | null => {
  if (!blockNumber) return null
  const m = BLOCK_RE.exec(blockNumber)
  return m ? m[1].toUpperCase() : null
}

const findStreet = (...texts: Array<string | null | undefined>): string | null => {
  for (const t of texts) {
    if (!t) continue
    const m = STREET_RE.exec(t)
    if (m) return m[1].toUpperCase().replace(/\s+/g, ' ').trim()
  }
  return null
}

const findPoi = (...texts: Array<string | null | undefined>): string | null => {
  const combined = texts.filter(Boolean).join(' ').toLowerCase()
  if (!combined) return null
  for (const [alias, query] of POI_ALIASES) {
    if (combined.includes(alias)) return query
  }
  return null
}

// OneMap's address index matches abbreviated postal forms ("YISHUN AVE 11")
const abbrevStreet = (street: string): string =>
  street.toUpperCase()
    .replace(/\bAVENUE\b/g, 'AVE')
    .replace(/\bSTREET\b/g, 'ST')
    .replace(/\bRING\s+ROAD\b/g, 'RING RD')
    .replace(/\bDRIVE\b/g, 'DR')

function buildQueries(
  blockNumber?: string | null,
  areaName?: string | null,
  extraText?: string | null,
): string[] {
  const queries: string[] = []
  const block  = cleanBlock(blockNumber)
  const street = findStreet(areaName, blockNumber)
  const poi    = findPoi(blockNumber, areaName, extraText)

  if (block && street) {
    const ab = abbrevStreet(street)
    queries.push(`${block} ${ab}`)
    if (ab !== street) queries.push(`${block} ${street}`)
    queries.push(`BLK ${block} ${ab}`)
  } else if (block) {
    queries.push(`${block} YISHUN`, `BLK ${block} YISHUN`)
  }

  if (!block && blockNumber && /\d/.test(blockNumber) && STREET_RE.test(blockNumber)) {
    const raw = blockNumber.trim().toUpperCase().replace(/\s+/g, ' ')
    queries.push(abbrevStreet(raw))
    if (abbrevStreet(raw) !== raw) queries.push(raw)
  }

  if (poi) queries.push(poi)
  if (street) queries.push(street)

  return queries
}

// Places OneMap has no record for — a hardcoded coordinate keyed on the exact
// query, checked before the API call. Keep in sync with geocoding.py's
// _VERIFIED_COORDS. Yishun Dam: OSM way (natural=dam); OneMap "YISHUN DAM"
// fuzzy-matched a temple 3.4 km away.
const VERIFIED_COORDS: Record<string, [number, number]> = {
  'YISHUN DAM': [1.42509, 103.85747],
}

const MATCH_STOPWORDS = new Set(['YISHUN', 'SINGAPORE', 'BLK', 'BLOCK', 'THE', 'OF', 'AND', 'AT'])

const toks = (s: string): Set<string> => new Set((s || '').toUpperCase().match(/[A-Z0-9]+/g) || [])

// OneMap search is fuzzy and always ranks something first, so an unindexed
// place silently resolves to an unrelated neighbour inside the box. Require a
// distinctive shared token — a wrong pin is worse than a missing one.
function resultMatchesQuery(query: string, r: any): boolean {
  const want = [...toks(query)].filter(t => !MATCH_STOPWORDS.has(t))
  if (want.length === 0) return true
  const hay = toks([r.SEARCHVAL, r.ROAD_NAME, r.ADDRESS, r.BLK_NO, r.BUILDING].join(' '))
  return want.some(t => hay.has(t))
}

async function onemapLookup(query: string): Promise<[number, number] | null> {
  const verified = VERIFIED_COORDS[query.trim().toUpperCase()]
  if (verified) return verified
  try {
    const params = new URLSearchParams({
      searchVal: query, returnGeom: 'Y', getAddrDetails: 'Y', pageNum: '1',
    })
    const res = await fetch(`${BASE_URL}?${params}`, {
      signal: AbortSignal.timeout(8000),
    })
    if (!res.ok) return null
    const results = (await res.json())?.results ?? []
    const hit = results.find((r: any) => resultMatchesQuery(query, r))
    if (hit) {
      const lat = parseFloat(hit.LATITUDE)
      const lon = parseFloat(hit.LONGITUDE)
      if (!isNaN(lat) && !isNaN(lon) && withinYishun(lat, lon)) return [lat, lon]
    }
  } catch { /* network error / timeout — treat as no result */ }
  return null
}

/**
 * Geocode an incident at publish time. Returns [latitude, longitude] or null.
 * Failure must never block an approval — callers publish with null coords
 * (no pin) when this returns null.
 */
/**
 * "khoo-teck-puat-hospital-opens-yishun-2010" -> spaced prose, so the slug can
 * be mined for a place-name like the title is. The slug routinely carries the
 * only location a story has: its headline is written around the event while
 * the slug keeps the place. Callers pass `${title} ${deslug(slug)}`.
 */
export function deslug(slug?: string | null): string {
  return (slug ?? '').replace(/-/g, ' ')
}

export async function geocodeIncident(
  blockNumber?: string | null,
  areaName?: string | null,
  extraText?: string | null,
): Promise<[number, number] | null> {
  for (const query of buildQueries(blockNumber, areaName, extraText)) {
    const coords = await onemapLookup(query)
    if (coords) return coords
    await new Promise(r => setTimeout(r, 500))  // OneMap rate limit
  }
  return null
}
