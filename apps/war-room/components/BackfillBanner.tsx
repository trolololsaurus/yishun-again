'use client'

import { useState } from 'react'

interface BackfillStats {
  run_at?:            unknown
  scraped?:           unknown
  auto_published?:    unknown
  queued_for_review?: unknown
  updates_found?:     unknown
  rejected?:          unknown
  errors?:            unknown
  [key: string]: unknown
}

interface Props {
  stats:         BackfillStats
  runAt:         string
  highConfCount: number   // pending backfill items with agent_confidence >= 0.85
  lowConfCount:  number   // pending backfill items with agent_confidence < 0.60
  highConfIds:   string[]
  lowConfIds:    string[]
}

export function BackfillBanner({
  stats,
  runAt,
  highConfCount,
  lowConfCount,
  highConfIds,
  lowConfIds,
}: Props) {
  const [loading, setLoading]   = useState<'high' | 'low' | null>(null)
  const [done, setDone]         = useState<{ high?: number; low?: number }>({})
  const [error, setError]       = useState<string | null>(null)

  const runDate = runAt
    ? new Date(runAt).toLocaleDateString('en-SG', {
        day: '2-digit', month: 'short', year: 'numeric',
        hour: '2-digit', minute: '2-digit',
      })
    : '—'

  async function bulkAction(tier: 'high' | 'low') {
    const ids = tier === 'high' ? highConfIds : lowConfIds
    if (!ids.length) return

    setLoading(tier)
    setError(null)

    try {
      const res = await fetch('/api/backfill-bulk', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ action: tier === 'high' ? 'approve' : 'reject', ids }),
      })

      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.error ?? `HTTP ${res.status}`)
      }

      const result = await res.json()
      setDone(prev => ({ ...prev, [tier]: result.updated ?? ids.length }))
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Unknown error')
    } finally {
      setLoading(null)
    }
  }

  return (
    <div className="mb-6 border border-border bg-surface p-4">
      {/* Header row */}
      <div className="flex items-center gap-3 mb-3">
        <span
          className="font-body font-bold text-text-secondary border border-border px-2 py-0.5"
          style={{ fontSize: '12px', letterSpacing: '0.1em' }}
        >
          BACKFILL COMPLETE
        </span>
        <span className="font-body text-text-secondary" style={{ fontSize: '13px' }}>
          {runDate}
        </span>
      </div>

      {/* Stats row */}
      <div className="flex flex-wrap gap-8 font-body mb-4" style={{ fontSize: '13px' }}>
        <div>
          <div className="text-text-secondary mb-0.5" style={{ fontSize: '12px' }}>SCRAPED</div>
          <div className="text-text-primary font-bold">{String(stats.scraped ?? '—')}</div>
        </div>
        <div>
          <div className="text-text-secondary mb-0.5" style={{ fontSize: '12px' }}>AUTO-PUBLISHED</div>
          <div className="text-green font-bold">{String(stats.auto_published ?? '—')}</div>
        </div>
        <div>
          <div className="text-text-secondary mb-0.5" style={{ fontSize: '12px' }}>QUEUED FOR REVIEW</div>
          <div className="text-yellow font-bold">{String(stats.queued_for_review ?? '—')}</div>
        </div>
        <div>
          <div className="text-text-secondary mb-0.5" style={{ fontSize: '12px' }}>UPDATES FOUND</div>
          <div className="text-text-primary font-bold">{String(stats.updates_found ?? '—')}</div>
        </div>
        <div>
          <div className="text-text-secondary mb-0.5" style={{ fontSize: '12px' }}>REJECTED</div>
          <div className="text-text-secondary font-bold">{String(stats.rejected ?? '—')}</div>
        </div>
        {(stats.errors as number) > 0 && (
          <div>
            <div className="text-text-secondary mb-0.5" style={{ fontSize: '12px' }}>ERRORS</div>
            <div className="text-red font-bold">{String(stats.errors)}</div>
          </div>
        )}
      </div>

      {/* Bulk action row — only shown when there are actionable pending items */}
      {(highConfCount > 0 || lowConfCount > 0) && (
        <div className="border-t border-border pt-3 flex flex-wrap gap-3 items-center">
          <span className="font-body text-text-secondary" style={{ fontSize: '13px' }}>
            Bulk actions:
          </span>

          {highConfCount > 0 && (
            done.high !== undefined ? (
              <span className="font-body text-green" style={{ fontSize: '13px' }}>
                ✓ {done.high} approved
              </span>
            ) : (
              <button
                onClick={() => bulkAction('high')}
                disabled={loading !== null}
                className="font-body border border-green text-green px-3 py-1 hover:bg-green hover:text-bg disabled:opacity-50 transition-colors"
                style={{ fontSize: '13px' }}
              >
                {loading === 'high' ? 'APPROVING…' : `APPROVE ALL HIGH-CONFIDENCE (${highConfCount})`}
              </button>
            )
          )}

          {lowConfCount > 0 && (
            done.low !== undefined ? (
              <span className="font-body text-text-secondary" style={{ fontSize: '13px' }}>
                ✓ {done.low} rejected
              </span>
            ) : (
              <button
                onClick={() => bulkAction('low')}
                disabled={loading !== null}
                className="font-body border border-border text-text-secondary px-3 py-1 hover:border-red hover:text-red disabled:opacity-50 transition-colors"
                style={{ fontSize: '13px' }}
              >
                {loading === 'low' ? 'REJECTING…' : `REJECT ALL LOW-CONFIDENCE (${lowConfCount})`}
              </button>
            )
          )}

          {error && (
            <span className="font-body text-red" style={{ fontSize: '13px' }}>
              ✗ {error}
            </span>
          )}
        </div>
      )}
    </div>
  )
}
