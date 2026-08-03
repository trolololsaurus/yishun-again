// TypeScript types derived from the Supabase schema (spec §3).

export type Classification = 'heart' | 'clown' | 'dagger' | 'custom'
export type QueueStatus    = 'pending' | 'approved' | 'rejected' | 'escalated' | 'update' | 'update_approved' | 'update_rejected'
export type SourceType     = 'msm' | 'reddit' | 'signal' | 'reference'
export type TrainingAction = 'approve' | 'edit_approve' | 'reject' | 'pattern_confirmed' | 'pattern_dismissed'
// Kept in lockstep with the CHECK in packages/db/migrations/017 and with
// VALID_REASONS in app/api/queue/[id]/reject/route.ts. Adding a value needs all
// three, or the insert is silently rejected by Postgres (the failure mode
// migration 009 was written to fix).
export type RejectReason   = 'noise' | 'duplicate' | 'unverified' | 'too_thin' | 'legal_risk' | 'not_yishun'

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

// ── Image generation state (migration 014) ──────────────────────────────────
//
// Single source of truth for the vocabulary. It previously lived in three
// places that had already drifted — the 014 column comment, the doc, and an
// inline union in artGenerate.ts that was missing `no_image_final` entirely.
export type ImageStatus =
  | 'ok'              // rendered, uploaded, HEAD-verified
  | 'suppressed'      // guardrail #5 — TERMINAL, never retried, never rectified
  | 'refused'         // the safety filter refused every rung of the ladder
  | 'transient'       // network / timeout / 5xx exhausted its attempts
  | 'invalid'         // unusable bytes or the wrong aspect ratio
  | 'skipped'         // the pass-level attempt ceiling was reached
  | 'pending'         // never attempted (backfill, or the backend unconfigured)
  | 'no_image_final'  // operator published without one — TERMINAL

export interface ImageAttempt {
  n:       number
  prompt:  string
  outcome: string
  reason?: string
}

/**
 * The rectification queue. Mirrors `idx_incidents_image_status` in 014 exactly.
 *
 * Deliberately an allowlist rather than "everything except suppressed": a
 * `.in()` excludes guardrail-#5 rows by construction, where a `!==` filter is
 * one careless edit away from inverting. Do not widen this to
 * `.neq('image_status', 'ok')`.
 *
 * Excluded on purpose — `ok` needs nothing; `suppressed` and `no_image_final`
 * are terminal; `pending` was never attempted at all and is a backfill problem,
 * not a rectification one.
 */
export const RECTIFIABLE_STATUSES = ['refused', 'transient', 'invalid', 'skipped'] as const
export type RectifiableStatus = typeof RECTIFIABLE_STATUSES[number]

/**
 * Columns the /rectify server component selects.
 *
 * Lives here, NOT in RectifyCard.tsx, and that is load-bearing: RectifyCard is
 * a `'use client'` module, and a server component importing a runtime VALUE
 * from one receives a client-reference proxy rather than the string. It
 * typechecks perfectly and then dies inside supabase-js with
 * "(intermediate value).split is not a function". Types are erased so they
 * cross the boundary fine; constants do not.
 */
export const RECTIFY_COLUMNS =
  'id, title, slug, classification, custom_label, severity, area_name, block_number, ' +
  'pixel_art_url, image_status, image_prompt, image_attempts, published_at'

export interface RectifyItem {
  id:             string
  title:          string
  slug:           string
  classification: Classification
  custom_label:   string | null
  severity:       number | null
  area_name:      string | null
  block_number:   string | null
  pixel_art_url:  string | null
  image_status:   ImageStatus | null
  image_prompt:   string | null
  image_attempts: ImageAttempt[] | null
  published_at:   string | null
}

const IMAGE_STATUSES: readonly string[] = [
  'ok', 'suppressed', 'refused', 'transient',
  'invalid', 'skipped', 'pending', 'no_image_final',
]

/** Validate a status crossing a process boundary. A bare `as ImageStatus` cast
 *  would let a backend typo through unchecked and into the database. */
export function isImageStatus(v: unknown): v is ImageStatus {
  return typeof v === 'string' && IMAGE_STATUSES.includes(v)
}

export interface Incident {
  id:                  string
  created_at:          string
  published_at:        string | null
  incident_date:       string
  title:               string
  summary:             string
  classification:      Classification
  custom_label:        string | null
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
  image_status:        ImageStatus | null
  image_prompt:        string | null
  image_attempts:      ImageAttempt[] | null
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

// ── Monthly orchestrator report ─────────────────────────────────────────────
// Shape written by packages/agents/ops/monthly_report.py. Every section is
// optional and carries `available`: a section whose table could not be read is
// {available: false, reason}, which the page must render as "no data for this
// period" rather than as zero — a failed query and a quiet month are not the
// same fact.

export interface ReportSection {
  available?: boolean
  reason?:    string
}

export interface MonthlyReportBody {
  period?: {
    start?: string; end?: string; days?: number
    generated_at?: string; trigger?: string
  }
  ingestion?: ReportSection & {
    passes?: number; total_queued?: number
    degraded_passes?: number; degraded_rate?: number | null
    per_source?: {
      source: string; passes: number; fetched: number; fresh: number; novel: number
      queued: number; blocked: number; unavailable: number; degraded: number
      last_reason?: string | null
    }[]
    sources_blocked?: string[]
  }
  publishing?: ReportSection & {
    published?: number; auto_published?: number; operator_approved?: number
    auto_share?: number | null; split_available?: boolean
    by_classification?: Record<string, number>
    by_severity?: Record<string, number>
    mean_severity?: number | null
    recent?: {
      title?: string; slug?: string; classification?: string
      severity?: number; published_at?: string; auto?: boolean
    }[]
  }
  operator?: ReportSection & {
    by_action?: Record<string, number>
    operator_decisions?: number; approve?: number; edit_approve?: number
    reject?: number; unpublish?: number
    agent_decisions?: number; reverted?: number
    reviews_saved?: number; net_reviews_saved?: number
    minutes_saved?: number; minutes_per_review?: number
    autonomy_share?: number | null
  }
  learning?: ReportSection & {
    captured?: boolean; snapshots?: number; captured_at?: string
    sample_count?: number
    agreement_rate?: number | null; agreement_delta?: number | null
    mean_confidence?: number | null; confidence_delta?: number | null
    edit_rate?: number | null; reject_rate?: number | null
    auto_publish_count?: number; auto_publish_reverted?: number
    verdict?: string
    previous?: { captured_at?: string; agreement_rate?: number | null; verdict?: string } | null
    agreement_vs_previous_month?: number | null
  }
  reliability?: ReportSection & {
    runs?: number; runs_readable?: boolean
    ok?: number; degraded?: number; failed?: number; running?: number
    by_agent?: {
      agent: string; runs: number; ok: number; degraded: number; failed: number
      running: number; avg_duration_ms: number | null
    }[]
    events?: { error?: number; anomaly?: number }
    top_events?: { event: string; level: string; count: number }[]
  }
  health?: ReportSection & {
    checks?: number
    components?: {
      component: string; checks: number; worst_status: string
      last_status: string | null; last_checked_at: string | null; message: string | null
    }[]
    worst_status?: string | null
    cost_guard?: {
      status?: string; checked_at?: string | null; message?: string | null
      detail?: Record<string, unknown>
    } | null
  }
  notifications?: ReportSection & {
    total?: number; sent?: number
    by_kind?: {
      kind: string; total: number; sent: number; suppressed: number
      failed: number; disabled: number; pending: number
    }[]
  }
  previous_period?: {
    start?: string; end?: string
    published?: number | null; auto_published?: number | null
    operator_decisions?: number | null; total_queued?: number | null; passes?: number | null
  }
  changes?: Record<string, number>
  warnings?: string[]
}

export interface MonthlyReport {
  id:           string
  created_at:   string
  period_start: string
  period_end:   string
  report:       MonthlyReportBody
  summary_text: string
  emailed_at:   string | null
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
  // Optional operator free text. Stored in training_signals.reject_note and
  // deliberately NOT read by the learning loop — see migration 017 for why
  // free text must never reach reject_reason.
  note?: string
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
