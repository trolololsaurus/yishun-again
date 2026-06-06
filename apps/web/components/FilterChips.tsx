'use client'

import type { FilterState } from '@/lib/types'
import { classDisplay } from '@/lib/utils'

interface Props {
  activeFilter:    FilterState
  counts:          { heart: number; clown: number; dagger: number; total: number }
  onFilterChange:  (f: FilterState) => void
}

const CHIPS: Array<{ key: FilterState; label: string }> = [
  { key: 'all',    label: 'ALL'                  },
  { key: 'heart',  label: classDisplay('heart')  },
  { key: 'clown',  label: classDisplay('clown')  },
  { key: 'dagger', label: classDisplay('dagger') },
]

export function FilterChips({ activeFilter, counts, onFilterChange }: Props) {
  const c = counts ?? { heart: 0, clown: 0, dagger: 0, total: 0 }
  const countFor = (key: FilterState): number =>
    key === 'all' ? c.total : c[key]

  return (
    <div
      className="flex items-center gap-2 px-3 overflow-x-hidden"
      style={{ height: 48, borderBottom: '1px solid var(--color-border)' }}
    >
      {CHIPS.map(({ key, label }) => {
        const active = activeFilter === key
        return (
          <button
            key={key}
            onClick={() => onFilterChange(key)}
            aria-pressed={active}
            className={[
              'font-display whitespace-nowrap px-3 py-2 border transition-colors',
              active
                ? 'bg-[#803018] border-[#C07830] text-[#C07830]'
                : 'bg-transparent border-[#1E2D4A] text-[#7A8BAA] hover:border-[#C07830] hover:text-[#C07830]',
            ].join(' ')}
            style={{ fontSize: 10, lineHeight: 1 }}
          >
            {label} ({countFor(key)})
          </button>
        )
      })}
    </div>
  )
}
