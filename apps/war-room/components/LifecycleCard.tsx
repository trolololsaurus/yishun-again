'use client'

import { useState } from 'react'
import type { QueueItem, LifecycleNotificationContent } from '@/lib/types'

interface Props {
  item:        QueueItem
  onProcessed: (id: string) => void
}

export function LifecycleCard({ item, onProcessed }: Props) {
  const rc      = item.raw_content as LifecycleNotificationContent
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

  return (
    <article className="bg-surface border border-border rounded opacity-90">
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-3 border-b border-border">
        <span className="px-2 py-0.5 bg-text-secondary text-bg font-body font-bold uppercase"
              style={{ fontSize: '10px' }}>
          AUTO-CONCLUDED
        </span>
        <span className="font-body text-text-secondary text-sm">Lifecycle</span>
        <span className="ml-auto font-body text-text-secondary text-sm">
          {new Date(item.created_at).toLocaleString('en-SG')}
        </span>
      </div>

      {/* Body */}
      <div className="p-4 space-y-3">
        <div className="font-body font-bold text-text-primary" style={{ fontSize: '15px' }}>
          {rc.incident_title}
        </div>
        <div className="font-body text-text-secondary text-sm">
          {rc.concluded_reason || 'No new sources in 180 days.'}
          {' '}The story has been automatically concluded.
        </div>
        <div className="font-body text-text-secondary" style={{ fontSize: '11px' }}>
          Review if incorrect — the incident is still in the archive; only the DEVELOPING flag was removed.
        </div>
      </div>

      {/* Actions */}
      <div className="flex items-center gap-3 px-4 py-3 border-t border-border">
        {error && <span className="font-body text-red text-sm">{error}</span>}
        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={() => post('reopen')}
            disabled={loading}
            className="px-3 py-2 border border-yellow text-yellow font-body text-sm hover:bg-yellow hover:text-bg transition-colors disabled:opacity-50"
          >
            Reopen
          </button>
          <button
            onClick={() => post('confirm-close')}
            disabled={loading}
            className="px-4 py-2 bg-text-secondary text-bg font-body text-sm font-bold hover:opacity-90 transition-opacity disabled:opacity-50"
          >
            {loading ? '…' : 'Confirm Close ✓'}
          </button>
        </div>
      </div>
    </article>
  )
}
