export type Classification = 'heart' | 'clown' | 'dagger' | 'custom'
export type FilterState    = 'all'   | 'heart' | 'clown'  | 'dagger'

export interface SourceTimelineEntry {
  date:        string
  source_url:  string
  source_name: string
  headline:    string
  role?:       string  // 'initial' | 'update' | 'verdict' | 'correction' | 'follow_up'
}

export interface IncidentLink {
  incident_a: string
  incident_b: string
  link_type:  'related' | 'follow_up' | 'same_location'
}

export interface RelatedIncident {
  id:             string
  slug:           string
  title:          string
  classification: Classification
  incident_date:  string
  link_type:      'related' | 'follow_up' | 'same_location'
}

export interface Incident {
  id:                  string
  published_at:        string
  incident_date:       string
  title:               string
  summary:             string
  classification:      Classification
  severity:            number
  block_number:        string | null
  area_name:           string | null
  latitude:            number | null
  longitude:           number | null
  source_urls:         string[]
  corroboration_count: number
  edmw_signal_count:   number
  hype_meter:          number
  pixel_art_url:       string | null
  slug:                string
  seo_title:           string | null
  seo_description:     string | null
  is_published:        boolean
  chaos_contribution:  number | null
  agent_confidence:    number | null
  tags:                string[] | null
  deaths:              number | null
  injuries:            number | null
  is_milestone:        boolean
  milestone_type:      string | null
  milestone_value:     number | null
  // v1.5 consolidation fields
  is_developing:       boolean
  update_count:        number
  source_timeline:     SourceTimelineEntry[]
  first_reported_at:   string | null
  latest_source_role:  string | null
}

// Lightweight type for the incident feed list rows
export type IncidentRow = Pick<
  Incident,
  'id' | 'slug' | 'title' | 'classification' | 'severity' | 'hype_meter'
  | 'published_at' | 'area_name' | 'is_milestone' | 'milestone_type'
  | 'is_developing' | 'update_count' | 'first_reported_at'
>

// GeoJSON feature for MapLibre markers
export interface MapFeature {
  type:     'Feature'
  geometry: { type: 'Point'; coordinates: [number, number] }  // [lng, lat]
  properties: {
    id:             string
    slug:           string
    title:          string
    classification: Classification
    severity:       number
    hype_meter:     number
  }
}

export interface ChaosData {
  year:             number
  score:            number
  descriptor:       string
  counts:           { heart: number; clown: number; dagger: number; total: number }
  deaths:           number
  injuries:         number
  // All-time totals — used for map filter chip counts, unaffected by year selection
  allTimeCounts:    { heart: number; clown: number; dagger: number; total: number }
  availableYears:   number[]
}

