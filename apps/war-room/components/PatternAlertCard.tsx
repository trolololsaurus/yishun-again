'use client'

import { useState } from 'react'
import type { QueueItem, PatternAlertContent } from '@/lib/types'

const PATTERN_LABEL: Record<string, string> = {
  entity:     'ENTITY',
  crime_type: 'CRIME TYPE',
  location:   'LOCATION',
}

const PATTERN_DESC: Record<string, string> = {
  entity:     'Same named subject across multiple incidents',
  crime_type: 'Repeated serious incident type in the same area',
  location:   'Incident cluster in the same location',
}

interface Props {
  item:            QueueItem
  relatedPreviews: Record<string, { title: string; slug: string }>
  siteUrl:         string
  onProcessed:     (id: string) => void
}

export function PatternAlertCard({ item, relatedPreviews, siteUrl, onProcessed }: Props) {
  const rc = item.raw_content as unknown as PatternAlertContent

  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState<string | null>(null)

  async function post(endpoint: string) {
    setLoading(true); setError(null)
    try {
      const res = await fetch(`/api/queue/${item.id}/${endpoint}`, { method: 'POST' })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.error ?? `HTTP ${res.status}`)
      }
      onProcessed(item.id)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unknown error')
      setLoading(false)
    }
  }

  const incidentIds    = rc.incident_ids    ?? []
  const incidentTitles = rc.incident_titles ?? []
  const patternType    = rc.pattern_type
  const patternValue   = rc.pattern_value
  const windowDays     = rc.window_days

  return (
    <article className="bg-surface border border-orange-500/50 rounded">
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-3 border-b border-orange-500/30">
        <span className="px-2 py-0.5 bg-orange-500 text-bg font-bold uppercase text-xs">
          PATTERN ALERT
        </span>
        <span className="font-bold text-orange-400 uppercase tracking-wide text-xs">
          {PATTERN_LABEL[patternType] ?? patternType}
        </span>
        <span className="ml-auto text-text-secondary text-sm">
          {new Date(item.created_at).toLocaleString('en-SG')}
        </span>
      </div>

      {/* Pattern summary */}
      <div className="p-4 space-y-4">
        <div>
          <div className="text-text-secondary mb-1 uppercase tracking-widest text-xs">
            {PATTERN_DESC[patternType] ?? 'Pattern detected'}
          </div>
          <div className="font-bold text-orange-300 text-lg">
            {patternValue}
          </div>
          <div className="text-text-secondary mt-1 text-xs">
            {incidentIds.length} incidents · {windowDays}-day window
          </div>
        </div>

        {/* Incident list */}
        <div>
          <div className="text-text-secondary mb-2 uppercase tracking-widest text-xs">
            Incidents in pattern
          </div>
          <ol className="space-y-1">
            {incidentIds.map((incId, i) => {
              const preview = relatedPreviews[incId]
              const title   = preview?.title ?? incidentTitles[i] ?? incId
              const slug    = preview?.slug  ?? ''
              const href    = slug ? `${siteUrl}/incidents/${slug}` : undefined
              return (
                <li key={incId} className="flex items-start gap-2">
                  <span className="text-text-secondary flex-none mt-0.5 text-xs">
                    {i + 1}.
                  </span>
                  {href ? (
                    <a href={href} target="_blank" rel="noopener noreferrer"
                       className="text-amber-lt hover:underline leading-snug text-sm">
                      {title}
                    </a>
                  ) : (
                    <span className="text-text-primary leading-snug text-sm">
                      {title}
                    </span>
                  )}
                </li>
              )
            })}
          </ol>
        </div>
      </div>

      {/* Actions */}
      <div className="flex items-center gap-2 px-4 py-3 border-t border-orange-500/30 flex-wrap">
        {error && <span className="text-red text-sm w-full mb-1">{error}</span>}

        <button
          onClick={() => post('dismiss-alert')}
          disabled={loading}
          className="px-3 py-2 border border-border text-text-secondary text-sm hover:border-red hover:text-red transition-colors disabled:opacity-50"
        >
          Dismiss
        </button>

        <div className="ml-auto flex items-center gap-2">
          {patternType === 'entity' && (
            <button
              onClick={() => post('note-profile')}
              disabled={loading}
              className="px-3 py-2 border border-yellow text-yellow text-sm hover:bg-yellow hover:text-bg transition-colors disabled:opacity-50"
            >
              Note for Profile
            </button>
          )}
          <button
            onClick={() => post('link-pattern')}
            disabled={loading}
            className="px-4 py-2 bg-orange-600 text-white text-sm font-bold hover:bg-orange-500 transition-colors disabled:opacity-50"
          >
            {loading ? '…' : 'Link Incidents ✓'}
          </button>
        </div>
      </div>
    </article>
  )
}
