'use client'

import type { FilterState } from '@/lib/types'
import { classIcon, classLabel } from '@/lib/utils'

interface Props {
  activeFilter:    FilterState
  counts:          { heart: number; clown: number; dagger: number; total: number }
  onFilterChange:  (f: FilterState) => void
}

const CHIPS: Array<{ key: FilterState; icon: string; word: string }> = [
  { key: 'all',    icon: '',                 word: 'ALL'              },
  { key: 'heart',  icon: classIcon('heart'), word: classLabel('heart')  },
  { key: 'clown',  icon: classIcon('clown'), word: classLabel('clown')  },
  { key: 'dagger', icon: classIcon('dagger'),word: classLabel('dagger') },
]

export function FilterChips({ activeFilter, counts, onFilterChange }: Props) {
  const c = counts ?? { heart: 0, clown: 0, dagger: 0, total: 0 }
  const countFor = (key: FilterState): number =>
    key === 'all' ? c.total : c[key]

  return (
    <div
      className="flex items-center gap-1.5 md:gap-2 px-2 md:px-3 overflow-x-hidden"
      style={{ height: 48, borderBottom: '1px solid var(--color-border)' }}
    >
      {CHIPS.map(({ key, icon, word }) => {
        const active = activeFilter === key
        return (
          <button
            key={key}
            onClick={() => onFilterChange(key)}
            aria-pressed={active}
            aria-label={`${word} (${countFor(key)})`}
            className={[
              'font-display whitespace-nowrap px-2 md:px-3 py-2 border transition-colors',
              active
                ? 'bg-[#803018] border-[#C07830] text-[#C07830]'
                : 'bg-transparent border-[#1E2D4A] text-[#7A8BAA] hover:border-[#C07830] hover:text-[#C07830]',
            ].join(' ')}
            style={{ fontSize: 10, lineHeight: 1 }}
          >
            {/* Full label on md+, emoji-only on mobile so four chips fit a phone. */}
            {icon
              ? <>{icon}<span className="hidden md:inline"> {word}</span></>
              : word}
            {' '}({countFor(key)})
          </button>
        )
      })}
    </div>
  )
}
