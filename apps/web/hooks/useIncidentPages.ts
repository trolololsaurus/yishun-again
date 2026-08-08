'use client'

import { useState, useEffect, useRef, useCallback } from 'react'

const DEFAULT_PAGE_SIZE = 20

interface Options<T> {
  /** Changing this resets to page 0 (filter/year/severity encoded as a string). */
  queryKey:      string
  /** Builds the /api/incidents URL for a given 0-based page. */
  buildUrl:      (page: number) => string
  /** Optional SSR seed for the FIRST queryKey; re-fetched immediately on mount. */
  initialItems?: T[]
  pageSize?:     number
}

interface Result<T> {
  items:    T[]
  loading:  boolean
  hasMore:  boolean
  loadMore: () => void
}

/**
 * Paged incident fetching shared by IncidentFeed and TimelineClient — the two
 * carried byte-identical page/hasMore/loadMore machinery, so it lives here once.
 * The caller owns the scroll trigger (an IntersectionObserver sentinel) and just
 * calls loadMore(); this hook owns the data.
 *
 * Two invariants preserved from the originals:
 *  - **Reset on a new query happens during render**, not in an effect — the
 *    documented replacement for a reset effect, so there is no frame showing the
 *    previous query's rows (https://react.dev/learn/you-might-not-need-an-effect).
 *  - **Only the newest request may write state** (reqRef/inFlight tickets), so
 *    switching filters twice quickly can't land the first result on top of the
 *    second, and a page-0 reset in flight can't be appended to by a stale
 *    load-more. setState only ever runs in a fetch continuation, per
 *    react-hooks/set-state-in-effect.
 */
export function useIncidentPages<T>({
  queryKey, buildUrl, initialItems, pageSize = DEFAULT_PAGE_SIZE,
}: Options<T>): Result<T> {
  const [items,   setItems]   = useState<T[]>(initialItems ?? [])
  const [page,    setPage]    = useState(0)
  const [hasMore, setHasMore] = useState((initialItems?.length ?? 0) === pageSize)
  // `initialItems` is the SSR seed for the initial query; the page-0 effect below
  // re-fetches immediately (SSR page 0 may be scoped differently — e.g. all-time
  // vs the selected year), so starting `true` is honest and needs no mount setState.
  const [loading, setLoading] = useState(true)

  const [renderedQuery, setRenderedQuery] = useState(queryKey)
  if (queryKey !== renderedQuery) {
    setRenderedQuery(queryKey)
    setItems([])
    setPage(0)
    setHasMore(true)
    setLoading(true)
  }

  const reqRef   = useRef(0)
  const inFlight = useRef(false)

  // Page 0 for the current query — the only effect that touches the network.
  useEffect(() => {
    const req = ++reqRef.current
    inFlight.current = true

    fetch(buildUrl(0))
      .then(res => {
        // 429/5xx returns {error} — don't spread it into the list.
        if (!res.ok) throw new Error(String(res.status))
        return res.json() as Promise<T[]>
      })
      .then(data => {
        if (req !== reqRef.current) return
        setItems(data)
        setPage(0)
        setHasMore(data.length === pageSize)
      })
      .catch(() => { if (req === reqRef.current) setHasMore(false) })
      .finally(() => {
        if (req !== reqRef.current) return   // superseded — the newer request owns the flag
        inFlight.current = false
        setLoading(false)
      })
  }, [buildUrl, pageSize])

  // Append the next page. Driven by the caller's scroll trigger, never an effect.
  const loadMore = useCallback(async () => {
    if (inFlight.current) return
    const next = page + 1
    const req  = ++reqRef.current
    inFlight.current = true
    setLoading(true)
    try {
      const res = await fetch(buildUrl(next))
      if (req !== reqRef.current) return
      if (!res.ok) { setHasMore(false); return }
      const data = (await res.json()) as T[]
      if (req !== reqRef.current) return
      setItems(prev => [...prev, ...data])
      setPage(next)
      if (data.length < pageSize) setHasMore(false)
    } catch {
      if (req === reqRef.current) setHasMore(false)
    } finally {
      if (req === reqRef.current) {
        inFlight.current = false
        setLoading(false)
      }
    }
  }, [buildUrl, page, pageSize])

  return { items, loading, hasMore, loadMore }
}
