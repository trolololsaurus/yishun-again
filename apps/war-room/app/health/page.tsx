import { supabase } from '@/lib/supabase'
import { isoDaysAgo, isDiscoverySource, primaryIdOf } from '@/lib/utils'
import type { ScraperHealth } from '@/lib/types'

export const revalidate = 0

function fmtMs(ms: number | null): string {
  if (ms == null) return '—'
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

function fmtDate(iso: string): string {
  return new Date(iso).toLocaleString('en-SG', {
    timeZone:  'Asia/Singapore',
    dateStyle: 'short',
    timeStyle: 'short',
  })
}

const STATUS_DOT: Record<string, string> = {
  ok:      '🟢',
  warning: '🟡',
  error:   '🔴',
}

const STATUS_CLS: Record<string, string> = {
  ok:      'text-green',
  warning: 'text-yellow',
  error:   'text-red',
}

const HEALTH_WINDOW_DAYS = 7

export default async function HealthPage() {
  const since150d   = isoDaysAgo(150)
  const since180d   = isoDaysAgo(180)
  const healthSince = isoDaysAgo(HEALTH_WINDOW_DAYS)

  const [scraperResult, developingResult, approachingResult, patternResult] = await Promise.all([
    supabase
      .from('scraper_health')
      .select('*')
      .gte('scraped_at', healthSince)
      .order('scraped_at', { ascending: false })
      .limit(200),
    supabase
      .from('incidents')
      .select('id', { count: 'exact', head: true })
      .eq('is_developing', true)
      .eq('is_published', true),
    supabase
      .from('incidents')
      .select('id', { count: 'exact', head: true })
      .eq('is_developing', true)
      .eq('is_published', true)
      .lte('published_at', since150d)
      .gte('published_at', since180d),
    supabase
      .from('pattern_alerts')
      .select('id', { count: 'exact', head: true })
      .eq('status', 'pending'),
  ])

  const { data: allRows, error } = scraperResult
  const developingCount   = developingResult.count  ?? 0
  const approachingCount  = approachingResult.count ?? 0
  const pendingAlerts     = patternResult.count     ?? 0

  if (error) {
    return (
      <div className="text-red text-sm">
        Failed to load health data: {error.message}
      </div>
    )
  }

  const seen = new Set<string>()
  const scrapers: ScraperHealth[] = []
  for (const row of (allRows ?? [])) {
    if (!seen.has(row.source_name)) {
      seen.add(row.source_name)
      scrapers.push(row as ScraperHealth)
    }
  }

  if (scrapers.length === 0) {
    return (
      <div>
        <h1 className="font-bold text-yellow text-lg mb-6">PIPELINE HEALTH</h1>
        <div className="text-text-secondary text-sm">
          No health data in the last {HEALTH_WINDOW_DAYS} days. Rows are written once per
          source per ingestion pass — if the daily pass is running, this should not be empty.
        </div>
      </div>
    )
  }

  // A discovery adapter (sitemap/search) in error is NOT an outlet outage when
  // the outlet's primary scraper is healthy this window — the recent-news spine
  // is intact, only the deeper archive window is degraded. Demote it out of the
  // red ERROR tier so red means "the outlet's own feed failed" (see queue panel
  // and lib/utils.isDiscoverySource for the full rationale).
  const okIds = new Set(scrapers.filter(s => s.status === 'ok').map(s => s.source_name))
  const isDemoted = (s: ScraperHealth): boolean =>
    s.status === 'error' && isDiscoverySource(s.source_name) && okIds.has(primaryIdOf(s.source_name))

  const green      = scrapers.filter(s => s.status === 'ok').length
  const yellow     = scrapers.filter(s => s.status === 'warning').length
  const red        = scrapers.filter(s => s.status === 'error' && !isDemoted(s)).length
  const discovery  = scrapers.filter(isDemoted).length

  const fetchedAt = new Date().toLocaleString('en-SG', {
    timeZone:  'Asia/Singapore',
    dateStyle: 'short',
    timeStyle: 'short',
  })

  return (
    <div>
      <div className="flex items-baseline gap-4 mb-2">
        <h1 className="font-bold text-yellow text-lg">PIPELINE HEALTH</h1>
        <span className="text-text-secondary text-sm">as of {fetchedAt} SGT</span>
      </div>

      {/* Lifecycle counters */}
      <div className="mb-8 border border-border bg-surface p-4">
        <div className="text-text-secondary mb-3 text-xs uppercase tracking-widest">
          Lifecycle
        </div>
        <div className="flex gap-10 text-sm">
          <div>
            <div className="text-text-secondary mb-1 text-xs">DEVELOPING</div>
            <div className="text-cyan-400 font-bold text-base">{developingCount}</div>
          </div>
          <div>
            <div className="text-text-secondary mb-1 text-xs">NEAR TIMEOUT (&gt;150d)</div>
            <div className={`font-bold text-base ${approachingCount > 0 ? 'text-yellow' : 'text-text-secondary'}`}>
              {approachingCount}
            </div>
          </div>
          <div>
            <div className="text-text-secondary mb-1 text-xs">PATTERN ALERTS</div>
            <div className={`font-bold text-base ${pendingAlerts > 0 ? 'text-orange-400' : 'text-text-secondary'}`}>
              {pendingAlerts}
            </div>
          </div>
        </div>
      </div>

      {/* Status legend + explainer */}
      <div className="mb-6">
        <div className="flex gap-8 mb-3 text-sm">
          <span className="text-green font-bold">🟢 HEALTHY ({green})</span>
          <span className="text-yellow font-bold">🟡 WARNING ({yellow})</span>
          <span className="text-red font-bold">🔴 ERROR ({red})</span>
          {discovery > 0 && (
            <span className="text-text-secondary font-bold">🔵 DISCOVERY ONLY ({discovery})</span>
          )}
        </div>
        <div className="bg-surface border border-border p-4 text-xs text-text-secondary leading-relaxed max-w-2xl space-y-2">
          <p>
            <span className="text-green font-bold">HEALTHY</span> — scraper ran
            and returned results normally in its last pass.
          </p>
          <p>
            <span className="text-yellow font-bold">WARNING</span> — the source
            returned 0 Yishun-matching items for 30+ consecutive runs. Usually
            normal (most outlets don't cover Yishun daily), not a fault.
          </p>
          <p>
            <span className="text-red font-bold">ERROR</span> — the scraper
            failed: network timeout, HTTP error, blocked by the source, or a
            parsing failure. The reason column shows what went wrong.
          </p>
          <p>
            <span className="text-text-primary font-bold">🔵 DISCOVERY ONLY</span> — a
            secondary discovery adapter (a publisher's own sitemap or WP search,
            id ending <code>_sitemap</code>/<code>_search</code>) failed while
            the outlet's primary feed is HEALTHY. The outlet is still covered;
            only its deeper archive window is degraded — not an outage.
          </p>
          <p>
            <span className="text-text-primary font-bold">7D AVG</span> — average
            scrape duration over the last 7 days. A sudden spike suggests the
            source is slow to respond or the scraper is retrying.
          </p>
        </div>
      </div>

      {/* Per-scraper table */}
      <div className="overflow-x-auto">
        <table className="w-full text-sm border-collapse">
          <thead>
            <tr className="text-text-secondary border-b border-border text-xs uppercase tracking-widest">
              <th className="text-left py-2 pr-6">Source</th>
              <th className="text-left py-2 pr-6">Type</th>
              <th className="text-left py-2 pr-6">Last run (SGT)</th>
              <th className="text-right py-2 pr-6">Items</th>
              <th className="text-left py-2 pr-6">Status</th>
              <th className="text-right py-2 pr-6">Consec. zeros</th>
              <th className="text-right py-2">7D avg</th>
            </tr>
          </thead>
          <tbody>
            {scrapers.map(s => (
              <tr key={s.id} className="border-b border-border hover:bg-surface transition-colors">
                <td className="py-3 pr-6 text-text-primary">{s.source_name}</td>
                <td className="py-3 pr-6 text-text-secondary text-xs uppercase">
                  {s.source_type}
                </td>
                <td className="py-3 pr-6 text-text-secondary">{fmtDate(s.scraped_at)}</td>
                <td className="py-3 pr-6 text-right text-text-primary">{s.items_found}</td>
                <td className="py-3 pr-6">
                  {isDemoted(s) ? (
                    <>
                      <span className="text-text-secondary font-bold">🔵 DISCOVERY ONLY</span>
                      <div className="text-text-secondary mt-1 text-xs">
                        {s.status_reason ? `${s.status_reason} · ` : ''}
                        {primaryIdOf(s.source_name)} primary feed OK, outlet still covered
                      </div>
                    </>
                  ) : (
                    <>
                      <span className={`${STATUS_CLS[s.status]} font-bold`}>
                        {STATUS_DOT[s.status]} {s.status.toUpperCase()}
                      </span>
                      {s.status_reason && (
                        <div className="text-text-secondary mt-1 text-xs">
                          {s.status_reason}
                        </div>
                      )}
                    </>
                  )}
                </td>
                <td className="py-3 pr-6 text-right">
                  <span className={s.consecutive_zeros >= 3 ? 'text-yellow font-bold' : 'text-text-secondary'}>
                    {s.consecutive_zeros}
                  </span>
                </td>
                <td className="py-3 text-right text-text-secondary">{fmtMs(s.avg_duration_7d)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
