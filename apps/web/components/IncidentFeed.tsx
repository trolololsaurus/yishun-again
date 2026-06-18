'use client'

import { useState, useEffect, useRef, useCallback } from 'react'
import { FixedSizeList, type ListChildComponentProps } from 'react-window'
import { IncidentCard } from './IncidentCard'
import type { FilterState, Incident } from '@/lib/types'

const ITEM_HEIGHT = 152  // extra 20px for verdict duration line on concluded incidents
const PAGE_SIZE   = 20

type Row = Pick<Incident, 'id' | 'slug' | 'title' | 'classification' | 'custom_label' | 'severity'
  | 'hype_meter' | 'published_at' | 'incident_date' | 'area_name' | 'is_milestone'
  | 'is_developing' | 'update_count' | 'first_reported_at'
  | 'source_timeline' | 'latest_source_role'>

interface Props {
  initialItems: Row[]
  activeFilter: FilterState
  selectedYear: number | null
}

export function IncidentFeed({ initialItems, activeFilter, selectedYear }: Props) {
  const [items,      setItems]      = useState<Row[]>(initialItems)
  const [page,       setPage]       = useState(0)
  const [hasMore,    setHasMore]    = useState(initialItems.length === PAGE_SIZE)
  const [loading,    setLoading]    = useState(false)
  const [listHeight, setListHeight] = useState(600)
  const containerRef                = useRef<HTMLDivElement>(null)

  // Reset when filter or year changes
  useEffect(() => {
    setItems([])
    setPage(-1)       // triggers the load below
    setHasMore(true)
  }, [activeFilter, selectedYear])

  const loadPage = useCallback(async (pageNum: number) => {
    if (loading) return
    setLoading(true)
    try {
      const params = new URLSearchParams({ page: String(pageNum), limit: String(PAGE_SIZE) })
      if (activeFilter !== 'all') params.set('classification', activeFilter)
      if (selectedYear)           params.set('year', String(selectedYear))

      const res  = await fetch(`/api/incidents?${params}`)
      const data = (await res.json()) as Row[]

      setItems(prev => pageNum === 0 ? data : [...prev, ...data])
      setPage(pageNum)
      if (data.length < PAGE_SIZE) setHasMore(false)
    } finally {
      setLoading(false)
    }
  }, [activeFilter, selectedYear, loading])

  // page === -1 signals a filter reset — load page 0
  useEffect(() => {
    if (page === -1) loadPage(0)
  }, [page]) // eslint-disable-line react-hooks/exhaustive-deps

  // Measure container for FixedSizeList
  useEffect(() => {
    if (!containerRef.current) return
    const ro = new ResizeObserver(([entry]) => {
      const h = entry.contentRect.height
      if (h > 0) setListHeight(h)
    })
    ro.observe(containerRef.current)
    return () => ro.disconnect()
  }, [])

  // Load more when near the bottom
  const onItemsRendered = useCallback(
    ({ visibleStopIndex }: { visibleStopIndex: number }) => {
      if (visibleStopIndex >= items.length - 3 && hasMore && !loading) {
        loadPage(page + 1)
      }
    },
    [items.length, hasMore, loading, page, loadPage]
  )

  const Row = ({ index, style }: ListChildComponentProps) => {
    if (index >= items.length) {
      return (
        <div style={style} className="flex items-center justify-center">
          <span className="font-body text-text-secondary" style={{ fontSize: '14px' }}>
            {loading ? 'Loading…' : 'No more incidents.'}
          </span>
        </div>
      )
    }
    return <IncidentCard incident={items[index]} style={style} />
  }

  const feedHeader = selectedYear
    ? `Showing ${selectedYear} · ${items.length} loaded`
    : `Most recent first`

  return (
    <div className="flex flex-col h-full">
      <div className="px-4 py-2 border-b border-border flex items-center justify-between">
        <span className="font-body text-text-secondary" style={{ fontSize: '14px' }}>
          {feedHeader}
        </span>
        {selectedYear && (
          <button
            className="font-body text-amber-lt hover:underline"
            onClick={() => {/* year reset is handled in HomeClient */}}
            style={{ fontSize: '14px' }}
          >
            clear
          </button>
        )}
      </div>

      <div ref={containerRef} style={{ flex: 1, minHeight: 0 }}>
        <FixedSizeList
          height={listHeight}
          itemCount={hasMore ? items.length + 1 : items.length}
          itemSize={ITEM_HEIGHT}
          width="100%"
          onItemsRendered={onItemsRendered as any}
        >
          {Row}
        </FixedSizeList>
      </div>
    </div>
  )
}
