'use client'

import { useState, useEffect, useRef, useCallback } from 'react'
import { IncidentCard }   from '@/components/IncidentCard'
import { classDisplay }   from '@/lib/utils'
import type { FilterState, Incident } from '@/lib/types'

type Row = Pick<Incident, 'id' | 'slug' | 'title' | 'classification' | 'custom_label' | 'severity'
  | 'corroboration_count' | 'published_at' | 'area_name' | 'is_milestone'
  | 'is_developing' | 'update_count' | 'first_reported_at'
  | 'incident_date' | 'source_timeline' | 'latest_source_role'>

const PAGE_SIZE = 20

export function TimelineClient() {
  const [items,      setItems]      = useState<Row[]>([])
  const [page,       setPage]       = useState(-1)
  const [hasMore,    setHasMore]    = useState(true)
  const [loading,    setLoading]    = useState(false)
  const [filter,     setFilter]     = useState<FilterState>('all')
  const [minSev,     setMinSev]     = useState(1)
  const [year,       setYear]       = useState<string>('')
  const sentinelRef                 = useRef<HTMLDivElement>(null)

  // Reset on filter change
  useEffect(() => {
    setItems([])
    setPage(-1)
    setHasMore(true)
  }, [filter, minSev, year])

  const loadMore = useCallback(async (pageNum: number) => {
    if (loading) return
    setLoading(true)
    try {
      const p = new URLSearchParams({ page: String(pageNum) })
      if (filter !== 'all') p.set('classification', filter)
      if (year)             p.set('year', year)
      const res  = await fetch(`/api/incidents?${p}`)
      const data = (await res.json()) as Row[]

      setItems(prev => pageNum === 0 ? data : [...prev, ...data])
      setPage(pageNum)
      if (data.length < PAGE_SIZE) setHasMore(false)
    } finally {
      setLoading(false)
    }
  }, [loading, filter, year])

  useEffect(() => { if (page === -1) loadMore(0) }, [page]) // eslint-disable-line

  // Intersection observer for infinite scroll
  useEffect(() => {
    const el = sentinelRef.current
    if (!el) return
    const obs = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting && hasMore && !loading) loadMore(page + 1)
    }, { rootMargin: '200px' })
    obs.observe(el)
    return () => obs.disconnect()
  }, [hasMore, loading, page, loadMore])

  return (
    <>
      {/* Filters */}
      <div className="flex flex-wrap gap-2 mb-4">
        {(['all', 'heart', 'clown', 'dagger'] as FilterState[]).map(f => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={[
              'min-h-[48px] px-4 py-2 border font-body',
              filter === f ? 'border-amber-lt text-amber-lt' : 'border-border text-text-secondary',
            ].join(' ')}
            style={{ fontSize: '16px' }}
          >
            {f === 'all' ? 'ALL' : classDisplay(f)}
          </button>
        ))}
        <select
          value={minSev}
          onChange={e => setMinSev(Number(e.target.value))}
          className="min-h-[48px] px-3 font-body bg-bg border border-border text-text-secondary"
          style={{ fontSize: '16px' }}
        >
          {[1,2,3,4,5].map(n => <option key={n} value={n}>Sev ≥ {n}</option>)}
        </select>
        <input
          type="number"
          placeholder="Year"
          value={year}
          min={2020} max={2100}
          onChange={e => setYear(e.target.value)}
          className="min-h-[48px] w-24 px-3 font-body bg-bg border border-border text-text-secondary"
          style={{ fontSize: '16px' }}
        />
      </div>

      {/* Incident list */}
      <div className="border border-border flex-1">
        {items.map(inc => <IncidentCard key={inc.id} incident={inc} />)}
        {loading && (
          <div className="px-4 py-6 text-center font-body text-text-secondary" style={{ fontSize: '14px' }}>
            Loading…
          </div>
        )}
        {!hasMore && items.length > 0 && (
          <div className="px-4 py-6 text-center font-body text-text-secondary" style={{ fontSize: '14px' }}>
            End of archive · {items.length} incidents
          </div>
        )}
        {!hasMore && items.length === 0 && (
          <div className="px-4 py-6 text-center font-body text-text-secondary" style={{ fontSize: '16px' }}>
            No incidents match your filters.
          </div>
        )}
        <div ref={sentinelRef} style={{ height: 1 }} />
      </div>
    </>
  )
}
