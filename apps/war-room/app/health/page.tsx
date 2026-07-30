import { supabase } from '@/lib/supabase'
import { isoDaysAgo } from '@/lib/utils'
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

export default async function HealthPage() {
  const since150d = isoDaysAgo(150)
  const since180d = isoDaysAgo(180)

  const [scraperResult, developingResult, approachingResult, patternResult] = await Promise.all([
    supabase
      .from('scraper_health')
      .select('*')
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
      <div className="font-body text-red text-sm">
        Failed to load health data: {error.message}
      </div>
    )
  }

  // Latest row per source (rows ordered DESC — first hit per source is newest)
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
        <h1 className="font-body font-bold text-yellow text-lg mb-6">PIPELINE HEALTH</h1>
        <div className="font-body text-text-secondary text-sm">
          No health data yet. Run the pipeline once to populate.
        </div>
      </div>
    )
  }

  const green  = scrapers.filter(s => s.status === 'ok').length
  const yellow = scrapers.filter(s => s.status === 'warning').length
  const red    = scrapers.filter(s => s.status === 'error').length

  const fetchedAt = new Date().toLocaleString('en-SG', {
    timeZone:  'Asia/Singapore',
    dateStyle: 'short',
    timeStyle: 'short',
  })

  return (
    <div>
      <div className="flex items-baseline gap-4 mb-2">
        <h1 className="font-body font-bold text-yellow text-lg">PIPELINE HEALTH</h1>
        <span className="font-body text-text-secondary text-sm">as of {fetchedAt} SGT</span>
      </div>

      {/* Lifecycle counters */}
      <div className="mb-8 border border-border bg-surface p-4">
        <div className="font-body text-text-secondary mb-3 uppercase tracking-widest" style={{ fontSize: '10px' }}>
          Lifecycle
        </div>
        <div className="flex gap-10 font-body text-sm">
          <div>
            <div className="text-text-secondary mb-1" style={{ fontSize: '10px' }}>DEVELOPING</div>
            <div className="text-cyan-400 font-bold text-base">{developingCount}</div>
          </div>
          <div>
            <div className="text-text-secondary mb-1" style={{ fontSize: '10px' }}>NEAR TIMEOUT (&gt;150d)</div>
            <div className={`font-bold text-base ${approachingCount > 0 ? 'text-yellow' : 'text-text-secondary'}`}>
              {approachingCount}
            </div>
          </div>
          <div>
            <div className="text-text-secondary mb-1" style={{ fontSize: '10px' }}>PATTERN ALERTS</div>
            <div className={`font-bold text-base ${pendingAlerts > 0 ? 'text-orange-400' : 'text-text-secondary'}`}>
              {pendingAlerts}
            </div>
          </div>
        </div>
      </div>

      {/* Scraper status counters */}
      <div className="flex gap-8 mb-8 font-body text-sm">
        <span className="text-green font-bold">🟢 HEALTHY ({green})</span>
        <span className="text-yellow font-bold">🟡 WARNING ({yellow})</span>
        <span className="text-red font-bold">🔴 ERROR ({red})</span>
      </div>

      {/* Per-scraper table */}
      <div className="overflow-x-auto">
        <table className="w-full font-body text-sm border-collapse">
          <thead>
            <tr className="text-text-secondary border-b border-border" style={{ fontSize: '10px' }}>
              <th className="text-left py-2 pr-6">SOURCE</th>
              <th className="text-left py-2 pr-6">TYPE</th>
              <th className="text-left py-2 pr-6">LAST RUN (SGT)</th>
              <th className="text-right py-2 pr-6">ITEMS</th>
              <th className="text-left py-2 pr-6">STATUS</th>
              <th className="text-right py-2 pr-6">CONSEC. ZEROS</th>
              <th className="text-right py-2">7D AVG</th>
            </tr>
          </thead>
          <tbody>
            {scrapers.map(s => (
              <tr key={s.id} className="border-b border-border hover:bg-surface transition-colors">
                <td className="py-3 pr-6 text-text-primary">{s.source_name}</td>
                <td className="py-3 pr-6 text-text-secondary uppercase" style={{ fontSize: '10px' }}>
                  {s.source_type}
                </td>
                <td className="py-3 pr-6 text-text-secondary">{fmtDate(s.scraped_at)}</td>
                <td className="py-3 pr-6 text-right text-text-primary">{s.items_found}</td>
                <td className="py-3 pr-6">
                  <span className={`${STATUS_CLS[s.status]} font-bold`}>
                    {STATUS_DOT[s.status]} {s.status.toUpperCase()}
                  </span>
                  {s.status_reason && (
                    <div className="text-text-secondary mt-1" style={{ fontSize: '10px' }}>
                      {s.status_reason}
                    </div>
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
