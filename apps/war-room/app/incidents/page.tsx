'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { classIcon, classLabel, classColor, severityDiamonds, hypeMeter } from '@/lib/utils'
import type { Incident } from '@/lib/types'

interface PageData { data: Incident[]; count: number; page: number; limit: number }

export default function IncidentsPage() {
  const [pageData, setPageData] = useState<PageData | null>(null)
  const [page, setPage]         = useState(1)
  const [loading, setLoading]   = useState(true)
  const [error, setError]       = useState<string | null>(null)
  const [unpublishing, setUnpublishing] = useState<string | null>(null)

  useEffect(() => {
    setLoading(true)
    fetch(`/api/incidents?page=${page}`)
      .then(r => r.json())
      .then(d => { setPageData(d); setLoading(false) })
      .catch(e => { setError(String(e)); setLoading(false) })
  }, [page])

  async function unpublish(id: string) {
    if (!confirm('Unpublish this incident?')) return
    setUnpublishing(id)
    try {
      const res = await fetch(`/api/incidents/${id}/unpublish`, { method: 'POST' })
      if (!res.ok) {
        // Don't drop the row from the list when the server refused — the
        // incident is still published.
        alert('Unpublish failed — the incident is still live.')
        return
      }
      setPageData(prev => prev ? {
        ...prev,
        data: prev.data.filter(i => i.id !== id),
      } : prev)
    } catch {
      alert('Unpublish failed — the incident is still live.')
    } finally {
      setUnpublishing(null)
    }
  }

  if (loading) return <div className="font-body text-text-secondary text-sm">Loading…</div>
  if (error)   return <div className="font-body text-red text-sm">{error}</div>
  if (!pageData) return null

  const { data: incidents, count, limit } = pageData
  const totalPages = Math.ceil((count ?? 0) / limit)

  return (
    <div>
      <h1 className="font-body font-bold text-yellow text-lg mb-6">
        INCIDENTS <span className="text-text-secondary">({count})</span>
      </h1>

      <table className="w-full font-body text-sm border-collapse">
        <thead>
          <tr className="border-b border-border text-text-secondary">
            <th className="text-left py-2 pr-4">Classification</th>
            <th className="text-left py-2 pr-4">Title</th>
            <th className="text-left py-2 pr-4">Sev</th>
            <th className="text-left py-2 pr-4">Hype</th>
            <th className="text-left py-2 pr-4">Published</th>
            <th className="text-left py-2 pr-4">Status</th>
            <th className="text-left py-2">Actions</th>
          </tr>
        </thead>
        <tbody>
          {incidents.map(inc => (
            <tr key={inc.id} className="border-b border-border hover:bg-surface/50">
              <td className={`py-2 pr-4 ${classColor(inc.classification, inc.custom_label)}`}>
                {classIcon(inc.classification, inc.custom_label)} {classLabel(inc.classification, inc.custom_label)}
              </td>
              <td className="py-2 pr-4 text-text-primary max-w-xs truncate">
                {inc.title}
              </td>
              <td className="py-2 pr-4 text-text-secondary">
                {severityDiamonds(inc.severity)}
              </td>
              <td className="py-2 pr-4 text-yellow">
                {hypeMeter(inc.hype_meter)}
              </td>
              <td className="py-2 pr-4 text-text-secondary">
                {inc.published_at
                  ? new Date(inc.published_at).toLocaleDateString('en-SG')
                  : '—'}
              </td>
              <td className="py-2 pr-4">
                {inc.is_published
                  ? <span className="text-green">● Live</span>
                  : <span className="text-text-secondary">● Draft</span>}
              </td>
              <td className="py-2">
                <div className="flex gap-2">
                  {inc.is_published ? (
                    // Live → real public page
                    <a
                      href={`https://www.yishunagain.com/incidents/${inc.slug}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-yellow hover:underline"
                    >
                      View
                    </a>
                  ) : (
                    // Draft → operator-only internal preview (public page 404s drafts)
                    <Link
                      href={`/incidents/${inc.slug}`}
                      className="text-text-secondary hover:text-text-primary hover:underline"
                    >
                      Preview
                    </Link>
                  )}
                  {inc.is_published && (
                    <button
                      onClick={() => unpublish(inc.id)}
                      disabled={unpublishing === inc.id}
                      className="text-red hover:underline disabled:opacity-50"
                    >
                      Unpublish
                    </button>
                  )}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {totalPages > 1 && (
        <div className="flex gap-3 mt-6 font-body text-sm">
          <button
            onClick={() => setPage(p => p - 1)}
            disabled={page === 1}
            className="px-3 py-1 border border-border text-text-secondary hover:text-text-primary disabled:opacity-30"
          >
            ← Prev
          </button>
          <span className="py-1 text-text-secondary">
            Page {page} / {totalPages}
          </span>
          <button
            onClick={() => setPage(p => p + 1)}
            disabled={page >= totalPages}
            className="px-3 py-1 border border-border text-text-secondary hover:text-text-primary disabled:opacity-30"
          >
            Next →
          </button>
        </div>
      )}
    </div>
  )
}
