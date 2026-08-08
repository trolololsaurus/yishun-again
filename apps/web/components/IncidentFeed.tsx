'use client'

import { useCallback, useEffect, useRef } from 'react'
import { NewsCard } from './NewsCard'
import { useIncidentPages } from '@/hooks/useIncidentPages'
import type { FilterState, Incident } from '@/lib/types'

const PAGE_SIZE = 20

type Row = Pick<Incident, 'id' | 'slug' | 'title' | 'summary' | 'classification' | 'custom_label' | 'severity'
  | 'corroboration_count' | 'published_at' | 'incident_date' | 'area_name' | 'is_milestone'
  | 'is_developing' | 'update_count' | 'first_reported_at'
  | 'source_urls' | 'source_timeline' | 'latest_source_role' | 'pixel_art_url'>

interface Props {
  initialItems: Row[]
  activeFilter: FilterState
  selectedYear: number | null
}

export function IncidentFeed({ initialItems, activeFilter, selectedYear }: Props) {
  const buildUrl = useCallback((pageNum: number) => {
    const params = new URLSearchParams({ page: String(pageNum), limit: String(PAGE_SIZE) })
    if (activeFilter !== 'all') params.set('classification', activeFilter)
    if (selectedYear)           params.set('year', String(selectedYear))
    return `/api/incidents?${params}`
  }, [activeFilter, selectedYear])

  const queryKey = `${activeFilter}|${selectedYear ?? ''}`
  const { items, loading, hasMore, loadMore } = useIncidentPages<Row>({
    queryKey, buildUrl, initialItems, pageSize: PAGE_SIZE,
  })

  // Infinite scroll: a 1px sentinel below the last card. The image cards are
  // variable-height (thumb + up to two title lines + an optional verdict line),
  // so the list is plain document flow now — the old react-window FixedSizeList
  // needed a constant row height it could no longer honour.
  const sentinelRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = sentinelRef.current
    if (!el) return
    const obs = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting && hasMore && !loading) loadMore()
    }, { rootMargin: '400px' })
    obs.observe(el)
    return () => obs.disconnect()
  }, [hasMore, loading, loadMore])

  const feedHeader = selectedYear
    ? `Showing ${selectedYear} · ${items.length} loaded`
    : 'Most recent first'

  return (
    <div className="flex flex-col h-full">
      <div className="px-4 py-2 border-b border-border flex-none">
        <span className="font-body text-text-secondary" style={{ fontSize: '14px' }}>
          {feedHeader}
        </span>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto">
        {items.map(inc => <NewsCard key={inc.id} incident={inc} />)}

        {loading && (
          <div className="px-4 py-6 text-center font-body text-text-secondary" style={{ fontSize: '14px' }}>
            Loading…
          </div>
        )}
        {!loading && !hasMore && items.length > 0 && (
          <div className="px-4 py-6 text-center font-body text-text-secondary" style={{ fontSize: '14px' }}>
            No more incidents.
          </div>
        )}
        {!loading && !hasMore && items.length === 0 && (
          <div className="px-4 py-6 text-center font-body text-text-secondary" style={{ fontSize: '16px' }}>
            No incidents match this filter.
          </div>
        )}

        <div ref={sentinelRef} style={{ height: 1 }} />
      </div>
    </div>
  )
}
