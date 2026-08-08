'use client'

import { classDisplay } from '@/lib/utils'
import type { FilterState } from '@/lib/types'

// Section header — Press Start 2P 11px, 0.1em tracking, uppercase, amber
function SectionHeader({ children }: { children: React.ReactNode }) {
  return (
    <div
      className="font-display uppercase mb-3"
      style={{ fontSize: 11, letterSpacing: '0.1em', color: 'var(--color-amber)' }}
    >
      {children}
    </div>
  )
}

// A breakdown row that is also the class filter: tap to filter, tap the active
// one again to clear back to ALL. Active state is a coloured ring so the layout
// never shifts (inactive keeps a transparent border of the same width).
function FilterRow({
  active, onClick, label, value, color,
}: { active: boolean; onClick: () => void; label: string; value: number; color: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className="w-full flex items-center justify-between mb-2 px-2 py-1.5 transition-colors"
      style={{
        border: `1px solid ${active ? color : 'transparent'}`,
        background: active ? 'rgba(255,255,255,0.05)' : 'transparent',
      }}
    >
      <span className="font-display text-left" style={{ fontSize: 10, color }}>{label}</span>
      <span className="font-display" style={{ fontSize: 20, color }}>{value}</span>
    </button>
  )
}

interface Counts { heart: number; clown: number; dagger: number; total: number }

interface Props {
  score:          number
  descriptor:     string
  counts:         Counts
  loading:        boolean
  error?:         boolean
  selectedYear:   number
  availableYears: number[]
  onYearChange:   (y: number) => void
  activeFilter:   FilterState
  onFilterChange: (f: FilterState) => void
}

export function ChaosPanel({
  score, descriptor, counts, loading, error, selectedYear, availableYears, onYearChange,
  activeFilter, onFilterChange,
}: Props) {
  // Defensive default — never crash if counts is momentarily absent.
  const c = counts ?? { heart: 0, clown: 0, dagger: 0, total: 0 }

  // The breakdown rows double as the class filter — they already list the
  // per-class counts, so the filter chips live here rather than duplicating the
  // numbers in a separate bar. Writing ?class= is all the feed/map need.
  const rows: Array<{ key: FilterState; label: string; value: number; color: string }> = [
    { key: 'heart',  label: classDisplay('heart'),  value: c.heart,  color: 'var(--color-good-vibes)' },
    { key: 'clown',  label: classDisplay('clown'),  value: c.clown,  color: 'var(--color-absurdities)' },
    { key: 'dagger', label: classDisplay('dagger'), value: c.dagger, color: 'var(--color-dark-events)' },
  ]

  return (
    <div className={`h-full bg-surface${loading ? ' opacity-60' : ''}`} style={{ borderLeft: '1px solid var(--color-border)' }}>

      {/* ── Year selector ─────────────────────────────────────── */}
      <div className="px-4 pt-4 pb-3" style={{ borderBottom: '1px solid var(--color-border)' }}>
        <label className="font-display block mb-2" style={{ fontSize: 10, color: 'var(--color-amber)' }}>
          YEAR
        </label>
        <select
          value={selectedYear}
          onChange={e => onYearChange(parseInt(e.target.value))}
          onWheel={e => (e.target as HTMLSelectElement).blur()}
          className="w-full font-body"
          style={{
            fontSize: 14, color: 'var(--color-amber)', background: 'var(--color-surface)',
            border: '1px solid var(--color-border)', minHeight: 40, padding: '0 8px',
          }}
        >
          {availableYears.map(y => (
            <option key={y} value={y}>{y}</option>
          ))}
        </select>
      </div>

      {error ? (
        /* ── Error state — surfaced loudly so a chaos failure is obvious ── */
        <div className="px-4 pt-4 pb-4" style={{ borderBottom: '1px solid var(--color-border)' }}>
          <SectionHeader>Chaos Index</SectionHeader>
          <div
            className="text-center py-4 px-3"
            style={{ background: 'var(--color-surface)', border: '2px solid var(--color-dark-events)' }}
          >
            <div className="font-display mb-2" style={{ fontSize: 13, color: 'var(--color-dark-events)', letterSpacing: '0.1em' }}>
              CHAOS DATA ERROR
            </div>
            <div className="font-body" style={{ fontSize: 13, color: 'var(--color-text-secondary)', lineHeight: 1.5 }}>
              Could not load stats for {selectedYear}. Check the console / API.
            </div>
          </div>
        </div>
      ) : (
        <>
          {/* ── Chaos Index ───────────────────────────────────────── */}
          <div className="px-4 pt-4 pb-4" style={{ borderBottom: '1px solid var(--color-border)' }}>
            <SectionHeader>Chaos Index</SectionHeader>

            <div
              className="text-center py-4 px-3"
              style={{
                background: 'var(--color-surface)',
                border: '2px solid var(--color-border)',
                boxShadow: 'inset 0 0 12px rgba(78, 205, 196, 0.08)',  // subtle teal cockpit glow
              }}
            >
              <div className="leading-none mb-3">
                <span className="font-display" style={{ fontSize: 48, color: 'var(--color-amber)' }}>{score}</span>
                <span className="font-display" style={{ fontSize: 20, color: 'var(--color-amber-dim)' }}>/100</span>
              </div>
              <div className="font-display" style={{ fontSize: 13, color: 'var(--color-good-vibes)', letterSpacing: '0.1em' }}>
                {descriptor.toUpperCase()}
              </div>
            </div>
          </div>

          {/* ── Incident Breakdown / class filter ─────────────────── */}
          <div className="px-4 pt-4 pb-4" style={{ borderBottom: '1px solid var(--color-border)' }}>
            <SectionHeader>Incident Breakdown</SectionHeader>
            <div className="font-body mb-3" style={{ fontSize: 13, color: 'var(--color-text-secondary)' }}>
              {selectedYear} · tap to filter
            </div>

            <FilterRow
              active={activeFilter === 'all'}
              onClick={() => onFilterChange('all')}
              label="ALL"
              value={c.total}
              color="var(--color-amber)"
            />
            {rows.map(({ key, label, value, color }) => (
              <FilterRow
                key={key}
                active={activeFilter === key}
                onClick={() => onFilterChange(activeFilter === key ? 'all' : key)}
                label={label}
                value={value}
                color={color}
              />
            ))}
          </div>
        </>
      )}

      {/* ── Legal disclaimer (kept at 10px) ───────────────────── */}
      <div className="px-4 pt-3 pb-4 font-body" style={{ fontSize: 10, color: 'var(--color-text-secondary)', lineHeight: 1.5 }}>
        <p className="mb-1">
          The satirical incident archive of Yishun, Nee Soon — Singapore&apos;s most eventful estate.
        </p>
        YISHUN AGAIN · SATIRE · ALL INCIDENTS SOURCED FROM PUBLIC MEDIA
      </div>

    </div>
  )
}
