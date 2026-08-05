'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { classIcon, classLabel, classColor, severityDiamonds, hypeMeter, uniqueSources, hypeFromSources } from '@/lib/utils'
import { ArtPromptModal } from '@/components/ArtPromptModal'
import type { Incident } from '@/lib/types'

type SortKey = 'published' | 'classification' | 'hype'
type SortDir = 'asc' | 'desc'

interface PageData {
  data:  Incident[]
  count: number
  page:  number
  limit: number
  sort:  SortKey
  dir:   SortDir
}

/** Identifies the view a payload describes. See `loading` below. */
function viewKey(page: number, sort: SortKey, dir: SortDir): string {
  return `${page}|${sort}|${dir}`
}

/** Declared at module scope, not inside the page: a component created during
 *  render is a fresh type on every pass, so React remounts it and it loses its
 *  state (react-hooks/static-components). */
function SortHeader(
  { label, sortKey, sort, dir, onSort }:
  { label: string; sortKey: SortKey; sort: SortKey; dir: SortDir; onSort: (k: SortKey) => void }
) {
  const active = sort === sortKey
  return (
    <th
      className="text-left py-2 pr-4"
      aria-sort={active ? (dir === 'asc' ? 'ascending' : 'descending') : 'none'}
    >
      <button
        onClick={() => onSort(sortKey)}
        className={`flex items-center gap-1 hover:text-text-primary ${active ? 'text-yellow' : ''}`}
        title={`Sort by ${label.toLowerCase()}`}
      >
        {label}
        <span className={active ? '' : 'opacity-25'}>{active && dir === 'asc' ? '▲' : '▼'}</span>
      </button>
    </th>
  )
}

export default function IncidentsPage() {
  const [pageData, setPageData] = useState<PageData | null>(null)
  const [page, setPage]         = useState(1)
  const [sort, setSort]         = useState<SortKey>('published')
  const [dir, setDir]           = useState<SortDir>('desc')
  const [error, setError]       = useState<{ view: string; message: string } | null>(null)
  const [unpublishing, setUnpublishing] = useState<string | null>(null)
  const [promptFor, setPromptFor] = useState<{ id: string; title: string } | null>(null)

  // `loading` is derived, not stored: it just means "neither the data nor the
  // error I'm holding belongs to the view I'm on". Storing it meant setting it
  // synchronously inside the effect — what react-hooks/set-state-in-effect
  // flags — which cost an extra render pass on every page change.
  // The API echoes back the page/sort/dir it served, which is what makes this
  // work; sort and dir are part of the key because re-sorting page 1 changes
  // the rows without changing the page number.
  const view = viewKey(page, sort, dir)
  const loading = (pageData ? viewKey(pageData.page, pageData.sort, pageData.dir) : null) !== view
                  && error?.view !== view

  useEffect(() => {
    let cancelled = false
    fetch(`/api/incidents?page=${page}&sort=${sort}&dir=${dir}`)
      .then(r => r.json())
      .then(d => { if (!cancelled) setPageData(d) })
      .catch(e => { if (!cancelled) setError({ view: viewKey(page, sort, dir), message: String(e) }) })
    return () => { cancelled = true }
  }, [page, sort, dir])

  // Clicking the active column flips direction; a new column starts descending
  // (newest / most corroborated first — the useful end of all three).
  // Always back to page 1: page 4 of the old order is meaningless in the new one.
  function sortBy(key: SortKey) {
    if (key === sort) setDir(d => (d === 'desc' ? 'asc' : 'desc'))
    else { setSort(key); setDir('desc') }
    setPage(1)
  }

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

  if (error?.view === view) return <div className="font-body text-red text-sm">{error.message}</div>

  const incidents = pageData?.data ?? []
  const count      = pageData?.count ?? 0
  const limit      = pageData?.limit ?? 50
  const totalPages = Math.ceil(count / limit)

  return (
    <div>
      <h1 className="font-body font-bold text-yellow text-lg mb-6">
        INCIDENTS <span className="text-text-secondary">({count})</span>
      </h1>

      <table className="w-full font-body text-sm border-collapse">
        <thead>
          <tr className="border-b border-border text-text-secondary">
            <SortHeader label="Classification" sortKey="classification" sort={sort} dir={dir} onSort={sortBy} />
            <th className="text-left py-2 pr-4">Title</th>
            <th className="text-left py-2 pr-4">Sev</th>
            <SortHeader label="Hype" sortKey="hype" sort={sort} dir={dir} onSort={sortBy} />
            <SortHeader label="Published" sortKey="published" sort={sort} dir={dir} onSort={sortBy} />
            <th className="text-left py-2 pr-4">Status</th>
            <th className="text-left py-2">Actions</th>
          </tr>
        </thead>
        <tbody>
          {incidents.map(inc => {
            // Counted, not trusted — the same rule the public page follows, so
            // the operator sees the bolt count the reader will get.
            const bolts = hypeFromSources(uniqueSources(inc.source_urls).length)
            return (
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
                {hypeMeter(bolts)}
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
                  <button
                    onClick={() => setPromptFor({ id: inc.id, title: inc.title })}
                    className="text-text-secondary hover:text-yellow hover:underline"
                  >
                    Prompt
                  </button>
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
            )
          })}
        </tbody>
      </table>

      {/* Kept below the table rather than replacing it: re-sorting should not
          blank the screen an operator is reading. */}
      {loading && (
        <div className="font-body text-text-secondary text-sm mt-4">Loading…</div>
      )}

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

      {promptFor && (
        <ArtPromptModal
          incidentId={promptFor.id}
          title={promptFor.title}
          onClose={() => setPromptFor(null)}
        />
      )}
    </div>
  )
}
