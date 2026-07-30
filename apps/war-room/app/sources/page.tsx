'use client'

import { useEffect, useState } from 'react'
import type { Source } from '@/lib/types'
import { safeHref } from '@/lib/utils'

const TYPE_COLOR: Record<string, string> = {
  msm:       'text-green',
  reddit:    'text-yellow',
  signal:    'text-purple',
  reference: 'text-text-secondary',
}

export default function SourcesPage() {
  const [sources,  setSources]  = useState<Source[]>([])
  const [loading,  setLoading]  = useState(true)
  const [updating, setUpdating] = useState<string | null>(null)

  useEffect(() => {
    fetch('/api/sources')
      .then(r => r.json())
      .then(d => { setSources(d); setLoading(false) })
  }, [])

  async function patch(id: string, update: Partial<Source>) {
    setUpdating(id)
    const res = await fetch(`/api/sources/${id}`, {
      method:  'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify(update),
    })
    if (res.ok) {
      setSources(prev => prev.map(s => s.id === id ? { ...s, ...update } : s))
    }
    setUpdating(null)
  }

  if (loading) return <div className="font-body text-text-secondary text-sm">Loading…</div>

  const unapproved = sources.filter(s => !s.approved_by_operator)
  const approved   = sources.filter(s =>  s.approved_by_operator)

  return (
    <div>
      <h1 className="font-body font-bold text-yellow text-lg mb-6">SOURCES</h1>

      {unapproved.length > 0 && (
        <section className="mb-8">
          <h2 className="font-body text-red text-sm uppercase tracking-widest mb-3">
            ⚠ Awaiting Approval ({unapproved.length})
          </h2>
          <SourceTable sources={unapproved} updating={updating} onPatch={patch} />
        </section>
      )}

      <section>
        <h2 className="font-body text-text-secondary text-sm uppercase tracking-widest mb-3">
          Active Sources ({approved.length})
        </h2>
        <SourceTable sources={approved} updating={updating} onPatch={patch} />
      </section>
    </div>
  )
}

function SourceTable({
  sources, updating, onPatch,
}: {
  sources:  Source[]
  updating: string | null
  onPatch:  (id: string, update: Partial<Source>) => void
}) {
  const TYPE_COLOR: Record<string, string> = {
    msm:       'text-green',
    reddit:    'text-yellow',
    signal:    'text-purple',
    reference: 'text-text-secondary',
  }

  return (
    <table className="w-full font-body text-sm border-collapse">
      <thead>
        <tr className="border-b border-border text-text-secondary">
          <th className="text-left py-2 pr-4">Name</th>
          <th className="text-left py-2 pr-4">Type</th>
          <th className="text-left py-2 pr-4">Interval</th>
          <th className="text-left py-2 pr-4">Reliability</th>
          <th className="text-left py-2 pr-4">Active</th>
          <th className="text-left py-2">Actions</th>
        </tr>
      </thead>
      <tbody>
        {sources.map(src => (
          <tr key={src.id} className="border-b border-border hover:bg-surface/50">
            <td className="py-2 pr-4">
              <a
                href={safeHref(src.url)}
                target="_blank"
                rel="noopener noreferrer"
                className="text-text-primary hover:text-yellow"
              >
                {src.name}
              </a>
              {src.discovery_notes && (
                <div className="text-text-secondary mt-0.5" style={{ fontSize: '10px' }}>
                  {src.discovery_notes.slice(0, 80)}
                </div>
              )}
            </td>
            <td className={`py-2 pr-4 ${TYPE_COLOR[src.type] ?? ''}`}>
              {src.type.toUpperCase()}
            </td>
            <td className="py-2 pr-4 text-text-secondary">
              {src.scrape_interval_minutes}m
            </td>
            <td className="py-2 pr-4 text-text-secondary">
              {src.reliability_score != null
                ? (src.reliability_score * 100).toFixed(0) + '%'
                : '—'}
            </td>
            <td className="py-2 pr-4">
              {src.is_active
                ? <span className="text-green">● On</span>
                : <span className="text-red">● Off</span>}
            </td>
            <td className="py-2">
              <div className="flex gap-3">
                {!src.approved_by_operator && (
                  <button
                    disabled={updating === src.id}
                    onClick={() => onPatch(src.id, { approved_by_operator: true })}
                    className="text-green hover:underline disabled:opacity-50"
                  >
                    Approve
                  </button>
                )}
                <button
                  disabled={updating === src.id}
                  onClick={() => onPatch(src.id, { is_active: !src.is_active })}
                  className={src.is_active ? 'text-red hover:underline disabled:opacity-50' : 'text-green hover:underline disabled:opacity-50'}
                >
                  {updating === src.id ? '…' : src.is_active ? 'Disable' : 'Enable'}
                </button>
              </div>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
