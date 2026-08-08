'use client'

import { useState, useRef } from 'react'
import { ChaosPanel }   from './ChaosPanel'
import { useChaosYear } from '@/hooks/useChaosYear'
import type { ChaosData } from '@/lib/types'

interface Props {
  chaos: ChaosData
}

/**
 * Mobile-only (`md:hidden`) Chaos Index as a bottom sheet: a slim always-visible
 * bar showing the score + descriptor, swiping (or tapping) up to reveal the full
 * panel — the same ChaosPanel the desktop sidebar renders, driven by the same
 * useChaosYear hook, so the year and stats stay in sync across the breakpoint.
 *
 * position:fixed sits above the body's `overflow:hidden`; anchored at bottom-0,
 * the panel grows upward as its max-height animates open. The desktop sidebar is
 * hidden at this width, so exactly one of the two is visible.
 */
export function BottomSheet({ chaos }: Props) {
  const {
    selectedYear, availableYears, selectedClass,
    stats, loading, error, onYearChange, onClassChange,
  } = useChaosYear(chaos)
  const [expanded, setExpanded] = useState(false)

  // Tap toggles; a vertical swipe on the bar opens/closes. `moved` suppresses the
  // click that a swipe also fires, so a swipe doesn't immediately toggle back.
  const startY = useRef<number | null>(null)
  const moved  = useRef(false)

  const onTouchStart = (e: React.TouchEvent) => { startY.current = e.touches[0].clientY; moved.current = false }
  const onTouchMove  = (e: React.TouchEvent) => {
    if (startY.current != null && Math.abs(e.touches[0].clientY - startY.current) > 10) moved.current = true
  }
  const onTouchEnd = (e: React.TouchEvent) => {
    if (startY.current == null) return
    const dy = e.changedTouches[0].clientY - startY.current
    if (dy < -30) setExpanded(true)
    else if (dy > 30) setExpanded(false)
    startY.current = null
  }
  const onClick = () => {
    if (moved.current) { moved.current = false; return }
    setExpanded(v => !v)
  }

  return (
    <div
      className="md:hidden fixed inset-x-0 bottom-0 z-[200] bg-surface"
      style={{ borderTop: '1px solid var(--color-border)', boxShadow: '0 -4px 12px rgba(0,0,0,0.45)' }}
    >
      {/* Panel — grows upward from behind the bar as it opens. */}
      <div
        className="overflow-y-auto transition-[max-height] duration-300 ease-out"
        style={{ maxHeight: expanded ? '70vh' : 0 }}
        aria-hidden={!expanded}
      >
        <ChaosPanel
          score={stats.score}
          descriptor={stats.descriptor}
          counts={stats.counts}
          loading={loading}
          error={error}
          selectedYear={selectedYear}
          availableYears={availableYears}
          onYearChange={onYearChange}
          activeFilter={selectedClass}
          onFilterChange={onClassChange}
        />
      </div>

      {/* Always-visible bar — tap or swipe to toggle. */}
      <button
        type="button"
        onClick={onClick}
        onTouchStart={onTouchStart}
        onTouchMove={onTouchMove}
        onTouchEnd={onTouchEnd}
        aria-expanded={expanded}
        aria-label="Chaos Index details"
        className="w-full block"
      >
        <div className="flex justify-center pt-2 pb-1">
          <span className="block rounded-full" style={{ width: 32, height: 4, background: 'var(--color-border)' }} />
        </div>
        <div className="flex items-center justify-between px-4 pb-2">
          <div className="flex items-baseline gap-2 min-w-0">
            <span className="font-display flex-none" style={{ fontSize: 11, color: 'var(--color-amber)', letterSpacing: '0.1em' }}>CHAOS</span>
            <span className="font-display flex-none" style={{ fontSize: 22, color: 'var(--color-amber)' }}>{stats.score}</span>
            <span className="font-display truncate" style={{ fontSize: 11, color: 'var(--color-good-vibes)', letterSpacing: '0.1em' }}>
              {stats.descriptor.toUpperCase()}
            </span>
          </div>
          <span className="font-body flex items-center gap-1 flex-none" style={{ fontSize: 12, color: 'var(--color-text-secondary)' }}>
            {selectedYear}<span aria-hidden>{expanded ? '▾' : '▴'}</span>
          </span>
        </div>
      </button>
    </div>
  )
}
