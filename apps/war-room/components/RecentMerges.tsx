'use client'

import { useState } from 'react'
import { safeHref } from '@/lib/utils'

export interface MergedRow {
  id:              string
  incidentTitle:   string
  incidentSlug:    string | null
  sourceUrl:       string
  headline:        string
  processedAt:     string | null
  hasSnapshot:     boolean
  /** Consolidation same-event confidence (the axis auto-merge gates on). */
  matchConfidence: number | null
  /** Stage 2 draft confidence. */
  draftConfidence: number | null
}

// The 0.95 bar auto-merge would apply at (AUTO_MERGE_*_CONFIDENCE defaults).
const AUTO_MERGE_BAR = 0.95

// Recently-applied updates (merges), each with an Undo. Once a merge is
// confirmed the queue row leaves the pending list, so without this the operator
// has no way to catch a wrongly-attached source — the merge is invisible in the
// live incident's source list. A merge from before undo shipped has no snapshot
// and can't be reverted here; it says so rather than offering a dead button.
export function RecentMerges({ initial, siteUrl }: { initial: MergedRow[]; siteUrl: string }) {
  const [rows, setRows]       = useState(initial)
  const [busy, setBusy]       = useState<string | null>(null)
  const [error, setError]     = useState<string | null>(null)

  if (rows.length === 0) return null

  // Readiness one-liner: would auto-merge have applied these? It fires only when
  // BOTH confidences clear 0.95, so count rows where both do. Rows with no
  // recorded match confidence (merged before the agents backend persisted it)
  // can't be judged — surfaced separately so the number is honest.
  const known = rows.filter(r => r.matchConfidence != null)
  const ready = known.filter(r =>
    (r.matchConfidence ?? 0) >= AUTO_MERGE_BAR && (r.draftConfidence ?? 0) >= AUTO_MERGE_BAR)
  const unknown = rows.length - known.length

  async function undo(id: string) {
    setBusy(id); setError(null)
    try {
      const res = await fetch(`/api/queue/${id}/revert-update`, { method: 'POST' })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.error ?? `HTTP ${res.status}`)
      }
      setRows(prev => prev.filter(r => r.id !== id))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Undo failed')
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="mb-6 border border-cyan-500/30 bg-surface p-4">
      <div className="text-text-secondary mb-1 uppercase tracking-widest text-sm">
        Recently merged updates
      </div>
      {/* Readiness one-liner: how many recent merges would auto-merge have caught
          at the 0.95 bar. When this is consistently "all", flipping
          AUTO_MERGE_ENABLED on is low-risk. */}
      <div className="text-text-secondary mb-3 text-xs">
        {known.length === 0
          ? <>Source-merge confidence not recorded yet (deploy the agents backend first) — hold off on auto-merge.</>
          : <><span className="text-green font-bold">{ready.length}</span>/{known.length} cleared both 0.95 gates (auto-merge would apply these){unknown > 0 ? `; ${unknown} unscored` : ''}.</>}
      </div>
      {error && <div className="text-red text-sm mb-2">{error}</div>}
      <div className="space-y-2">
        {rows.map(r => (
          <div key={r.id} className="flex items-center gap-3 border border-border bg-bg p-2 text-sm">
            <div className="min-w-0 flex-1">
              <div className="text-text-primary truncate">
                {r.incidentSlug
                  ? <a href={`${siteUrl}/incidents/${r.incidentSlug}`} target="_blank" rel="noopener noreferrer" className="hover:underline">{r.incidentTitle}</a>
                  : r.incidentTitle}
              </div>
              <a href={safeHref(r.sourceUrl)} target="_blank" rel="noopener noreferrer"
                 className="text-yellow text-xs hover:underline break-all">
                + {r.sourceUrl}
              </a>
            </div>
            <span
              className={`text-xs whitespace-nowrap font-bold ${
                r.matchConfidence == null ? 'text-text-secondary'
                : r.matchConfidence >= AUTO_MERGE_BAR ? 'text-green' : 'text-yellow'
              }`}
              title="Source-merge (same-event) confidence"
            >
              {r.matchConfidence == null ? 'match —' : `match ${(r.matchConfidence * 100).toFixed(0)}%`}
            </span>
            <span className="text-text-secondary text-xs whitespace-nowrap">
              {r.processedAt ? new Date(r.processedAt).toLocaleString('en-SG') : ''}
            </span>
            {r.hasSnapshot ? (
              <button
                onClick={() => undo(r.id)}
                disabled={busy === r.id}
                className="px-3 py-1 border border-red text-red text-xs hover:bg-red hover:text-bg transition-colors disabled:opacity-50"
              >
                {busy === r.id ? '…' : 'Undo merge'}
              </button>
            ) : (
              <span className="text-text-secondary text-xs italic" title="Merged before undo existed — reconcile by hand">
                no snapshot
              </span>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
