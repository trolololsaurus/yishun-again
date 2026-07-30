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
  const [page,       setPage]       = useState(0)
  const [hasMore,    setHasMore]    = useState(true)
  const [loading,    setLoading]    = useState(true)
  const [filter,     setFilter]     = useState<FilterState>('all')
  const [minSev,     setMinSev]     = useState(1)
  const [year,       setYear]       = useState<string>('')
  const sentinelRef                 = useRef<HTMLDivElement>(null)

  const query = `${filter}|${minSev}|${year}`

  // Reset for a new filter set *during render* rather than in an effect. React
  // re-runs the component immediately and throws the discarded output away, so
  // there is no cascading commit and no frame showing the old filter's rows.
  // https://react.dev/learn/you-might-not-need-an-effect
  const [renderedQuery, setRenderedQuery] = useState(query)
  if (query !== renderedQuery) {
    setRenderedQuery(query)
    setItems([])
    setPage(0)
    setHasMore(true)
    setLoading(true)
  }

  const buildUrl = useCallback((pageNum: number) => {
    const p = new URLSearchParams({ page: String(pageNum) })
    if (filter !== 'all') p.set('classification', filter)
    if (year)             p.set('year', year)
    if (minSev > 1)       p.set('min_severity', String(minSev))   // QA M1
    return `/api/incidents?${p}`
  }, [filter, year, minSev])

  // Only the newest request may write state — see IncidentFeed for the same guard.
  const reqRef   = useRef(0)
  const inFlight = useRef(false)

  // Page 0 for the current filter set. setState happens in the fetch
  // continuation, never synchronously in the effect body
  // (react-hooks/set-state-in-effect).
  useEffect(() => {
    const req = ++reqRef.current
    inFlight.current = true

    fetch(buildUrl(0))
      .then(res => {
        // 429/5xx returns {error} — don't spread it into the list.
        if (!res.ok) throw new Error(String(res.status))
        return res.json() as Promise<Row[]>
      })
      .then(data => {
        if (req !== reqRef.current) return
        setItems(data)
        setPage(0)
        setHasMore(data.length === PAGE_SIZE)
      })
      .catch(() => { if (req === reqRef.current) setHasMore(false) })
      .finally(() => {
        if (req !== reqRef.current) return
        inFlight.current = false
        setLoading(false)
      })
  }, [buildUrl])

  const loadMore = useCallback(async () => {
    if (inFlight.current) return
    const next = page + 1
    const req  = ++reqRef.current
    inFlight.current = true
    setLoading(true)
    try {
      const res  = await fetch(buildUrl(next))
      if (req !== reqRef.current) return
      // 429/5xx returns {error} — don't spread it into the list.
      if (!res.ok) { setHasMore(false); return }
      const data = (await res.json()) as Row[]
      if (req !== reqRef.current) return
      setItems(prev => [...prev, ...data])
      setPage(next)
      if (data.length < PAGE_SIZE) setHasMore(false)
    } catch {
      if (req === reqRef.current) setHasMore(false)
    } finally {
      if (req === reqRef.current) {
        inFlight.current = false
        setLoading(false)
      }
    }
  }, [buildUrl, page])

  // Intersection observer for infinite scroll
  useEffect(() => {
    const el = sentinelRef.current
    if (!el) return
    const obs = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting && hasMore && !loading) loadMore()
    }, { rootMargin: '200px' })
    obs.observe(el)
    return () => obs.disconnect()
  }, [hasMore, loading, loadMore])

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
