'use client'

import { useState, useRef } from 'react'
import { ChaosPanel }   from './ChaosPanel'
import { useChaosYear } from '@/hooks/useChaosYear'
import type { ChaosData } from '@/lib/types'

interface Props {
  chaos: ChaosData
}

/**
 * Mobile-only (`md:hidden`) Chaos Index as a bottom sheet.
 *
 * The always-visible handle carries the YEAR selector (the one control worth
 * having one tap away on the feed/map), not a score readout. Swiping or tapping
 * the grab handle expands the sheet upward to reveal the full panel — CHAOS
 * INDEX + INCIDENT BREAKDOWN — the same ChaosPanel the desktop sidebar renders
 * (minus its own YEAR block, which this header owns), driven by the same
 * useChaosYear hook so everything stays in sync across the breakpoint.
 *
 * Layout: position:fixed, bottom-0, sitting above the body's `overflow:hidden`.
 * The header (handle + YEAR) is first and always shown; the panel below it
 * animates its max-height 0→70vh. Because the container is bottom-anchored, the
 * panel grows the sheet UPWARD — so the expanded order top→bottom is YEAR, then
 * CHAOS INDEX, then INCIDENT BREAKDOWN, with the header pinned above the score.
 */
export function BottomSheet({ chaos }: Props) {
  const {
    selectedYear, availableYears, selectedClass,
    stats, loading, error, onYearChange, onClassChange,
  } = useChaosYear(chaos)
  const [expanded, setExpanded] = useState(false)

  // A vertical swipe on the grab handle opens/closes; a tap toggles. `moved`
  // suppresses the click a swipe also fires so it doesn't immediately toggle
  // back. The YEAR <select> is a sibling of the handle, not inside it, so
  // choosing a year never toggles the sheet.
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
  const onHandleClick = () => {
    if (moved.current) { moved.current = false; return }
    setExpanded(v => !v)
  }

  return (
    <div
      className="md:hidden fixed inset-x-0 bottom-0 z-[200] bg-surface"
      style={{ borderTop: '1px solid var(--color-border)', boxShadow: '0 -4px 12px rgba(0,0,0,0.45)' }}
    >
      {/* ── Always-visible header: grab handle + YEAR selector ────────────── */}
      <button
        type="button"
        onClick={onHandleClick}
        onTouchStart={onTouchStart}
        onTouchMove={onTouchMove}
        onTouchEnd={onTouchEnd}
        aria-expanded={expanded}
        aria-label="Chaos Index details"
        className="w-full flex items-center justify-center gap-2 pt-2 pb-1"
      >
        <span className="block rounded-full" style={{ width: 32, height: 4, background: 'var(--color-border)' }} />
        <span className="font-body" style={{ fontSize: 12, color: 'var(--color-text-secondary)' }} aria-hidden>
          {expanded ? '▾' : '▴'}
        </span>
      </button>

      <div className="px-4 pt-1 pb-3" style={{ borderBottom: '1px solid var(--color-border)' }}>
        <label className="font-display block mb-2 text-[12px]" style={{ color: 'var(--color-amber)' }}>
          YEAR
        </label>
        <select
          value={selectedYear}
          onChange={e => onYearChange(parseInt(e.target.value))}
          onWheel={e => (e.target as HTMLSelectElement).blur()}
          // 18px keeps it clear of the iOS zoom-on-focus threshold (16px).
          className="w-full font-body text-[18px]"
          style={{
            color: 'var(--color-amber)', background: 'var(--color-surface)',
            border: '1px solid var(--color-border)', minHeight: 48, padding: '0 8px',
          }}
        >
          {availableYears.map(y => (
            <option key={y} value={y}>{y}</option>
          ))}
        </select>
      </div>

      {/* ── Panel — CHAOS INDEX + BREAKDOWN, grows upward from behind the header. */}
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
          showYear={false}
        />
      </div>
    </div>
  )
}
