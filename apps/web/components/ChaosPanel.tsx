'use client'

import { classDisplay } from '@/lib/utils'

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

interface Counts { heart: number; clown: number; dagger: number; total: number }

interface Props {
  score:          number
  descriptor:     string
  counts:         Counts
  loading:        boolean
  selectedYear:   number
  availableYears: number[]
  onYearChange:   (y: number) => void
}

export function ChaosPanel({
  score, descriptor, counts, loading, selectedYear, availableYears, onYearChange,
}: Props) {
  // Defensive default — never crash if counts is momentarily absent.
  const c = counts ?? { heart: 0, clown: 0, dagger: 0, total: 0 }

  // INCIDENT BREAKDOWN rows — renamed labels + colours per spec.
  // counts are the SHARED year stats (same source as the filter chips).
  const breakdown: Array<{ label: string; value: number; color: string }> = [
    { label: classDisplay('heart'),  value: c.heart,  color: 'var(--color-good-vibes)' },
    { label: classDisplay('clown'),  value: c.clown,  color: 'var(--color-absurdities)' },
    { label: classDisplay('dagger'), value: c.dagger, color: 'var(--color-dark-events)' },
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

      {/* ── Incident Breakdown ────────────────────────────────── */}
      <div className="px-4 pt-4 pb-4" style={{ borderBottom: '1px solid var(--color-border)' }}>
        <SectionHeader>Incident Breakdown</SectionHeader>
        <div className="font-body mb-3" style={{ fontSize: 13, color: 'var(--color-text-secondary)' }}>
          {selectedYear}
        </div>
        {breakdown.map(({ label, value, color }) => (
          <div key={label} className="flex items-center justify-between mb-3">
            <span className="font-display" style={{ fontSize: 10, color }}>{label}</span>
            <span className="font-display" style={{ fontSize: 20, color }}>{value}</span>
          </div>
        ))}
      </div>

      {/* ── Legal disclaimer (kept at 10px) ───────────────────── */}
      <div className="px-4 pt-3 pb-4 font-body" style={{ fontSize: 10, color: 'var(--color-text-secondary)', lineHeight: 1.5 }}>
        YISHUN AGAIN · SATIRE · ALL INCIDENTS SOURCED FROM PUBLIC MEDIA
      </div>

    </div>
  )
}
