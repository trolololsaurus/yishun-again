import { Suspense } from 'react'
import { supabase } from '@/lib/supabase'
import { ChaosSidebar } from '@/components/ChaosSidebar'
import { BottomSheet }  from '@/components/BottomSheet'
import { computeChaosScore, chaosDescriptor } from '@/lib/utils'
import type { ChaosData } from '@/lib/types'

// ISR: the sidebar's SSR seed is the current-year snapshot, revalidated every
// 60s. A different year is fetched client-side (Decision A) — reading
// searchParams here would force dynamic rendering and lose this cache.
export const revalidate = 60

/**
 * Shared HUD chrome for the Feed (`/`) and Map (`/map`) routes: the flex shell
 * plus the Chaos Index sidebar, which persists across the two because this
 * layout does not unmount between them.
 *
 * The shell is static server-rendered markup; only the sidebar is a client
 * component (it reads `?year=` and re-fetches), so it sits behind a Suspense
 * boundary. The desktop-only 280px column matches the pre-split layout; the
 * mobile bottom sheet arrives in Phase 5.
 */
export default async function HudLayout({ children }: { children: React.ReactNode }) {
  const currentYear = new Date().getFullYear()

  const [{ data: yearRows }, { data: incidentDateRows }] = await Promise.all([
    // Current-year stats for the sidebar (score + breakdown).
    supabase
      .from('incidents')
      .select('classification,severity,deaths,injuries,published_at')
      .eq('is_published', true)
      .gte('incident_date', `${currentYear}-01-01`)
      .lt( 'incident_date', `${currentYear + 1}-01-01`),
    // Distinct incident years for the dropdown.
    supabase
      .from('incidents')
      .select('incident_date')
      .eq('is_published', true)
      .not('incident_date', 'is', null),
  ])

  const rows  = yearRows ?? []
  const score = computeChaosScore(rows)

  const counts = rows.reduce(
    (acc, r) => {
      // Custom (CULTURE) cards fold into GOOD VIBES (heart) for the breakdown and
      // the ALL total, so the Good Vibes filter shows them and its count matches.
      // They still never touch the Chaos score (computeChaosScore weight 0).
      const cls = r.classification
      if (cls === 'heart' || cls === 'custom') { acc.heart += 1; acc.total += 1 }
      else if (cls === 'clown')  { acc.clown  += 1; acc.total += 1 }
      else if (cls === 'dagger') { acc.dagger += 1; acc.total += 1 }
      return acc
    },
    { heart: 0, clown: 0, dagger: 0, total: 0 }
  )

  const deaths   = rows.reduce((s, r) => s + (r.deaths   ?? 0), 0)
  const injuries = rows.reduce((s, r) => s + (r.injuries ?? 0), 0)

  // Parse the year out of the YYYY-MM-DD string directly — new Date() would
  // read it as UTC and roll a Jan-1 SGT date back a year.
  const yearSet = new Set(
    (incidentDateRows ?? [])
      .map(r => parseInt(String(r.incident_date).slice(0, 4), 10))
      .filter(y => !isNaN(y))
  )
  yearSet.add(currentYear)
  const availableYears = [...yearSet].sort((a, b) => b - a)

  const chaos: ChaosData = {
    year:       currentYear,
    score,
    descriptor: chaosDescriptor(score),
    counts,
    deaths,
    injuries,
    availableYears,
  }

  return (
    <>
      <div className="flex-1 min-h-0 flex overflow-hidden">
        {/* Left column — the route's page (feed or map) fills the width. On
            mobile it reserves 128px at the bottom so content clears the collapsed
            bottom sheet (which is position:fixed and would otherwise overlap).
            The collapsed sheet is ~122px now that it carries the YEAR selector. */}
        <div className="flex-1 min-w-0 flex flex-col overflow-hidden pb-[128px] md:pb-0">
          {children}
        </div>

        {/* Desktop sidebar — 280px, hidden on mobile where the bottom sheet
            takes over. Scrolls internally. */}
        <aside className="hidden md:block flex-none h-full overflow-y-auto overflow-x-hidden" style={{ width: 280 }}>
          <Suspense fallback={null}>
            <ChaosSidebar chaos={chaos} />
          </Suspense>
        </aside>
      </div>

      {/* Mobile bottom sheet (md:hidden inside). Fixed to the viewport bottom,
          so it escapes the overflow-hidden shell above. */}
      <Suspense fallback={null}>
        <BottomSheet chaos={chaos} />
      </Suspense>
    </>
  )
}
