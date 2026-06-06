// TypeScript types derived from the Supabase schema (spec §3).

export type Classification = 'heart' | 'clown' | 'dagger' | 'custom'
export type QueueStatus    = 'pending' | 'approved' | 'rejected' | 'escalated' | 'update' | 'update_approved' | 'update_rejected'
export type SourceType     = 'msm' | 'reddit' | 'signal' | 'reference'
export type TrainingAction = 'approve' | 'edit_approve' | 'reject' | 'pattern_confirmed' | 'pattern_dismissed'
export type RejectReason   = 'noise' | 'duplicate' | 'unverified' | 'too_thin' | 'legal_risk'

export interface QueueItem {
  id:                      string
  created_at:              string
  raw_content:             Record<string, unknown>
  source_url:              string
  source_type:             SourceType
  proposed_classification: Classification | null
  proposed_severity:       number | null
  proposed_summary:        string | null
  proposed_title:          string | null
  proposed_pixel_prompt:   string | null
  proposed_slug:           string | null
  agent_confidence:        number | null
  corroboration_count:     number
  edmw_signal_count:       number
  status:                        QueueStatus
  processed_at:                  string | null
  incident_id:                   string | null
  update_target_incident_id:     string | null
}

// Notification type embedded in raw_content for lifecycle/pattern queue items
export type NotificationType = 'lifecycle_concluded' | 'pattern_alert'

// raw_content shape for lifecycle_concluded notification
export interface LifecycleNotificationContent {
  notification_type:  'lifecycle_concluded'
  incident_id:        string
  incident_title:     string
  incident_slug:      string
  concluded_reason:   string
}

// raw_content shape for pattern_alert notification
export interface PatternAlertContent {
  notification_type:  'pattern_alert'
  pattern_alert_id:   string
  pattern_type:       'entity' | 'crime_type' | 'location'
  pattern_value:      string
  incident_ids:       string[]
  incident_titles:    string[]
  window_days:        number
}

export interface PatternAlert {
  id:              string
  created_at:      string
  pattern_type:    'entity' | 'crime_type' | 'location'
  pattern_value:   string
  incident_ids:    string[]
  window_days:     number
  status:          'pending' | 'confirmed' | 'dismissed'
  operator_action: string | null
  resolved_at:     string | null
}

// Embedded in raw_content.agent_related_incidents by the pipeline
export interface AgentRelatedIncident {
  incident_id: string
  confidence:  number
  reason:      string
  link_type:   'related' | 'follow_up' | 'same_location'
  dismissed?:  boolean
}

// Lightweight incident snapshot passed to UpdateCard / related-incident banners
export interface IncidentPreview {
  id:             string
  title:          string
  summary:        string
  slug:           string
  classification: Classification
  severity:       number
  incident_date:  string
  source_urls:    string[]
  update_count:   number
  is_developing:  boolean
}

export interface Incident {
  id:                  string
  created_at:          string
  published_at:        string | null
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
  deaths:              number | null   // null = not mentioned; 0 = confirmed none; N = confirmed count
  injuries:            number | null
  is_milestone:        boolean
  milestone_type:      string | null
  milestone_value:     number | null
}

export interface Source {
  id:                    string
  name:                  string
  url:                   string
  type:                  SourceType
  is_active:             boolean
  scrape_interval_minutes: number
  reliability_score:     number | null
  added_at:              string
  approved_by_operator:  boolean
  discovery_notes:       string | null
}

export interface UtmEvent {
  id:            string
  incident_id:   string | null
  timestamp:     string
  utm_source:    string | null
  utm_medium:    string | null
  utm_campaign:  string | null
  geo_country:   string | null
  geo_city:      string | null
  vpn_suspected: boolean
  referrer:      string | null
}

export interface TrainingSignal {
  id:                       string
  incident_id:              string | null
  timestamp:                string
  action:                   TrainingAction
  reject_reason:            RejectReason | null
  original_draft:           string | null
  edited_draft:             string | null
  original_classification:  string | null
  edited_classification:    string | null
  original_severity:        number | null
  edited_severity:          number | null
  operator_changes:         Record<string, unknown> | null
  agent_confidence_was:     number | null
}

export type ScraperHealthStatus = 'ok' | 'warning' | 'error'

export interface ScraperHealth {
  id:                string
  scraped_at:        string
  source_name:       string
  source_type:       string
  items_found:       number
  items_passed_s1:   number
  errors:            string[] | null
  duration_ms:       number | null
  status:            ScraperHealthStatus
  status_reason:     string | null
  consecutive_zeros: number
  avg_duration_7d:   number | null
}

// Approve request body sent from QueueCard
export interface ApproveBody {
  title:           string
  summary:         string
  classification:  Classification
  severity:        number
  pixel_art_prompt: string
}

// Reject request body
export interface RejectBody {
  reason: RejectReason
}

export const DISMISS_CATEGORIES = {
  SAME_ENTITY_DIFFERENT_ACT: {
    label: 'Same person, different act',
    description: 'Same named individual but incidents are unrelated',
    autonomy_signal: 'entity_dedup',
  },
  LOCATION_COINCIDENCE: {
    label: 'Coincidental location overlap',
    description: 'Same area but incidents have no connection',
    autonomy_signal: 'location_dedup',
  },
  TEMPORAL_COINCIDENCE: {
    label: 'Timeframe overlap only',
    description: 'Happened around the same time but unrelated',
    autonomy_signal: 'temporal_dedup',
  },
  WRONG_ENTITY_MATCH: {
    label: 'Wrong entity match',
    description: 'Agent matched the wrong person or place',
    autonomy_signal: 'entity_extraction',
  },
  INSUFFICIENT_EVIDENCE: {
    label: 'Insufficient evidence of connection',
    description: 'Link is speculative, not enough to confirm',
    autonomy_signal: 'confidence_threshold',
  },
  OTHER: {
    label: 'Other',
    description: 'None of the above',
    autonomy_signal: 'other',
  },
} as const

export type DismissCategory = keyof typeof DISMISS_CATEGORIES
