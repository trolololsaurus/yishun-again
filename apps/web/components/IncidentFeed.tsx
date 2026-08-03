'use client'

import { useState, useEffect, useRef, useCallback } from 'react'
import { FixedSizeList, type ListChildComponentProps } from 'react-window'
import { IncidentCard } from './IncidentCard'
import type { FilterState, Incident } from '@/lib/types'

const ITEM_HEIGHT = 152  // extra 20px for verdict duration line on concluded incidents
const PAGE_SIZE   = 20

type Row = Pick<Incident, 'id' | 'slug' | 'title' | 'classification' | 'custom_label' | 'severity'
  | 'corroboration_count' | 'published_at' | 'incident_date' | 'area_name' | 'is_milestone'
  | 'is_developing' | 'update_count' | 'first_reported_at'
  | 'source_urls' | 'source_timeline' | 'latest_source_role'>

interface Props {
  initialItems: Row[]
  activeFilter: FilterState
  selectedYear: number | null
}

export function IncidentFeed({ initialItems, activeFilter, selectedYear }: Props) {
  const [items,      setItems]      = useState<Row[]>(initialItems)
  const [page,       setPage]       = useState(0)
  const [hasMore,    setHasMore]    = useState(initialItems.length === PAGE_SIZE)
  // `initialItems` is the SSR page 0, which is all-time — the client always
  // mounts with a year selected, so page 0 is re-fetched immediately. Starting
  // at `true` is therefore honest, and means mount needs no setState of its own.
  const [loading,    setLoading]    = useState(true)
  // Fallback only: what SSR and the first client render use before the callback
  // ref below measures the real container. Must stay non-zero so the server
  // still emits the first few rows as crawlable links.
  const [listHeight, setListHeight] = useState(600)

  const query = `${activeFilter}|${selectedYear ?? ''}`

  // Reset for a new filter/year *during render*, not in an effect. React re-runs
  // the component immediately without committing the discarded output, so there
  // is no cascading commit and no frame where the previous filter's rows are on
  // screen beneath the new filter. This is the documented replacement for a
  // reset effect: https://react.dev/learn/you-might-not-need-an-effect
  const [renderedQuery, setRenderedQuery] = useState(query)
  if (query !== renderedQuery) {
    setRenderedQuery(query)
    setItems([])
    setPage(0)
    setHasMore(true)
    setLoading(true)
  }

  const buildUrl = useCallback((pageNum: number) => {
    const params = new URLSearchParams({ page: String(pageNum), limit: String(PAGE_SIZE) })
    if (activeFilter !== 'all') params.set('classification', activeFilter)
    if (selectedYear)           params.set('year', String(selectedYear))
    return `/api/incidents?${params}`
  }, [activeFilter, selectedYear])

  // Every request takes a ticket; only the newest one is allowed to write state.
  // Switching filters twice in quick succession used to be able to land the
  // first filter's rows on top of the second's, and a page-0 reset that arrived
  // while a load-more was in flight could append to a list it never matched.
  const reqRef    = useRef(0)
  const inFlight  = useRef(false)

  // Page 0 for the current query — the only effect that touches the network.
  // Every setState below runs in the fetch continuation rather than synchronously
  // in the effect body, which is what react-hooks/set-state-in-effect asks for
  // (and is the rule's own "call setState in a callback" escape hatch).
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
        if (req !== reqRef.current) return   // superseded — the newer request owns the flag
        inFlight.current = false
        setLoading(false)
      })
  }, [buildUrl])

  // Append the next page. Driven by the scroll callback, never by an effect.
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

  // Measure the container for FixedSizeList.
  //
  // A callback ref rather than an effect + `useRef`, for two reasons. It runs
  // during commit with the node already in the DOM, so the very first paint uses
  // the real height instead of the 600px fallback. And it takes the first
  // measurement unconditionally: the old ResizeObserver-only version dropped
  // its initial callback whenever `contentRect.height` was still 0 (the `h > 0`
  // guard), and since the container's size never changed afterwards no second
  // callback ever arrived — the list stayed 600px tall inside a ~235px box,
  // overflowing into `overflow-hidden` and clipping its own scroll region.
  // React 19 runs the returned function as the ref's cleanup.
  const measureRef = useCallback((el: HTMLDivElement | null) => {
    if (!el) return
    const apply = () => setListHeight(el.getBoundingClientRect().height)
    apply()
    const ro = new ResizeObserver(apply)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // Load more when near the bottom
  const onItemsRendered = useCallback(
    ({ visibleStopIndex }: { visibleStopIndex: number }) => {
      if (visibleStopIndex >= items.length - 3 && hasMore && !loading) {
        loadMore()
      }
    },
    [items.length, hasMore, loading, loadMore]
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
      <div className="px-4 py-2 border-b border-border">
        <span className="font-body text-text-secondary" style={{ fontSize: '14px' }}>
          {feedHeader}
        </span>
      </div>

      <div ref={measureRef} style={{ flex: 1, minHeight: 0 }}>
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
