'use client'

import { useState, useEffect } from 'react'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { chaosDescriptor } from '@/lib/utils'
import { parseYear, patchedParams, buildHref } from '@/lib/params'
import type { ChaosData } from '@/lib/types'

interface Stats {
  score:      number
  descriptor: string
  counts:     { heart: number; clown: number; dagger: number; total: number }
}

interface Result {
  selectedYear:   number
  availableYears: number[]
  stats:          Stats
  loading:        boolean
  error:          boolean
  onYearChange:   (y: number) => void
}

/**
 * The Chaos Index year + per-year stats, shared by the desktop sidebar
 * (ChaosSidebar) and the mobile bottom sheet (BottomSheet) so both stay a single
 * implementation. Reads the year from `?year=`, writes it back on change, and
 * owns the per-year /api/chaos fetch. The SSR `chaos` seed covers the current
 * year with no fetch; a different year is fetched, with the reset done during
 * render (the codebase idiom) so setState only ever runs in the fetch
 * continuation (react-hooks/set-state-in-effect).
 */
export function useChaosYear(chaos: ChaosData): Result {
  const pathname     = usePathname()
  const router       = useRouter()
  const searchParams = useSearchParams()

  const currentYear  = chaos.year
  const selectedYear = parseYear(searchParams) ?? currentYear

  const [stats, setStats] = useState<Stats>({
    score:      chaos.score,
    descriptor: chaos.descriptor,
    counts:     chaos.counts,
  })
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState(false)

  const [trackedYear, setTrackedYear] = useState(currentYear)
  if (trackedYear !== selectedYear) {
    setTrackedYear(selectedYear)
    if (selectedYear === currentYear) {
      setStats({ score: chaos.score, descriptor: chaos.descriptor, counts: chaos.counts })
      setLoading(false)
      setError(false)
    } else {
      setLoading(true)
      setError(false)
    }
  }

  useEffect(() => {
    if (selectedYear === currentYear) return   // SSR seed applied during render
    let cancelled = false

    fetch(`/api/chaos?year=${selectedYear}`)
      .then(async r => {
        if (!r.ok) throw new Error(`chaos API ${r.status}`)
        return r.json()
      })
      .then(d => {
        if (cancelled) return
        if (!d || !d.counts) throw new Error('chaos API: malformed payload')
        setError(false)
        setStats({ score: d.score, descriptor: chaosDescriptor(d.score), counts: d.counts })
      })
      .catch(err => {
        if (cancelled) return
        console.error('[useChaosYear] stats fetch failed:', err)
        setError(true)
      })
      .finally(() => { if (!cancelled) setLoading(false) })

    return () => { cancelled = true }
  }, [selectedYear, currentYear])

  const onYearChange = (y: number) => {
    // Drop the param at the current year so the default view keeps a clean URL.
    const next = patchedParams(searchParams, { year: y === currentYear ? null : String(y) })
    router.replace(buildHref(pathname, next), { scroll: false })
  }

  return { selectedYear, availableYears: chaos.availableYears, stats, loading, error, onYearChange }
}
