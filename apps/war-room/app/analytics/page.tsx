'use client'

import { useEffect, useState, useCallback, useMemo } from 'react'

interface AutonomyCategory {
  total_decisions:      number
  operator_corrections: number
  error_rate:           number
  samples_needed:       number
  graduated:            boolean
  status:               'graduated' | 'in_training' | 'insufficient_data'
}

const AUTONOMY_UNLOCKS: Record<string, string> = {
  entity_dedup:         'Auto-dismiss same-entity-different-act links',
  location_dedup:       'Auto-dismiss coincidental location links',
  temporal_dedup:       'Auto-dismiss timeframe-only links',
  entity_extraction:    'Trust agent entity matching without review',
  confidence_threshold: 'Auto-approve links above threshold',
  role_assignment:      'Auto-assign source roles without review',
  classification:       'Auto-classify without operator confirmation',
  severity:             'Auto-assign severity without operator confirmation',
}

const STATUS_BADGE: Record<AutonomyCategory['status'], { icon: string; label: string; color: string }> = {
  graduated:         { icon: '✅', label: 'GRADUATED',        color: 'text-green' },
  in_training:       { icon: '⏳', label: 'IN TRAINING',      color: 'text-yellow' },
  insufficient_data: { icon: '❌', label: 'INSUFFICIENT DATA', color: 'text-text-secondary' },
}

interface AnalyticsData {
  utm_sources:   { source: string; count: number }[]
  geo_breakdown: { country: string; count: number }[]
  referrers:     { domain: string; count: number }[]
  total_events:  number
  training:      { action: string; count: number }[]
  queue_stats:   { pending: number; approved: number; rejected: number }
}

interface CloudflareData {
  days:          { date: string; visits: number; requests: number }[]
  countries:     { country: string; visits: number }[]
  referrers:     { host: string; visits: number }[]
  devices:       { device: string; visits: number }[]
  total_visits:  number
  total_requests: number
  errors:        string[]
}

interface IncidentRow {
  id: string
  title: string
  slug: string
  classification: string
  views_total: number
  views_7d: number
  shares: number
  avg_dwell_ms: number | null
  bounce_rate: number | null
  ctr_from_feed: number | null
  sessions: number
}

interface IncidentsData {
  window_days:   number
  trending_days: number
  feed_sessions: number
  site: {
    pageviews: number
    sessions: number
    shares: number
    avg_dwell_ms: number | null
    bounce_rate: number | null
  }
  incidents: IncidentRow[]
  truncated: boolean
}

function BarRow({ label, count, max, color }: { label: string; count: number; max: number; color: string }) {
  const pct = max > 0 ? Math.round((count / max) * 100) : 0
  return (
    <div className="flex items-center gap-3 mb-2">
      <span className="w-32 text-text-secondary text-sm truncate">{label}</span>
      <div className="flex-1 bg-border/30 h-4 relative">
        <div className={`h-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="w-12 text-right text-sm text-text-primary">{count}</span>
    </div>
  )
}

function StatTile({ value, label, color }: { value: string; label: string; color?: string }) {
  return (
    <div className="bg-surface border border-border px-6 py-4 text-center">
      <div className={`font-bold text-2xl ${color ?? 'text-text-primary'}`}>{value}</div>
      <div className="text-text-secondary text-sm mt-1">{label}</div>
    </div>
  )
}

const CLASS_COLORS: Record<string, string> = {
  dagger: 'text-purple',
  clown:  'text-yellow',
  heart:  'text-red',
  custom: 'text-culture',
}

const CLASS_EMOJI: Record<string, string> = {
  dagger: '🗡️',
  clown:  '🤡',
  heart:  '❤️',
  custom: '🎭',
}

function pct(n: number | null): string {
  return n === null ? '—' : `${Math.round(n * 100)}%`
}

function fmtDwell(ms: number | null): string {
  if (ms === null) return '—'
  const s = Math.round(ms / 1000)
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`
}

type SortKey = 'views_total' | 'views_7d' | 'shares' | 'bounce_rate' | 'avg_dwell_ms' | 'ctr_from_feed'

function useFetch<T>(url: string) {
  const [data, setData]       = useState<T | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError]     = useState<string | null>(null)

  const load = useCallback(() => {
    fetch(url)
      .then(r => r.ok ? r.json() : Promise.reject(r.statusText))
      .then(d  => { setData(d); setError(null); setLoading(false) })
      .catch(e => { setError(String(e)); setLoading(false) })
  }, [url])

  return { data, loading, error, load, setLoading }
}

export default function AnalyticsPage() {
  const analytics = useFetch<AnalyticsData>('/api/analytics')
  const cf         = useFetch<CloudflareData>('/api/analytics/cloudflare')
  const incidents  = useFetch<IncidentsData>('/api/analytics/incidents?days=30')
  const autonomy   = useFetch<Record<string, AutonomyCategory>>('/api/autonomy')

  const refreshAutonomy = useCallback(() => {
    autonomy.setLoading(true)
    autonomy.load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    analytics.load()
    cf.load()
    incidents.load()
    autonomy.load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const refreshAll = useCallback(() => {
    analytics.setLoading(true); analytics.load()
    cf.setLoading(true);        cf.load()
    incidents.setLoading(true); incidents.load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const anyLoading = analytics.loading || cf.loading || incidents.loading

  const [sortKey, setSortKey] = useState<SortKey>('views_total')
  const [sortAsc, setSortAsc] = useState(false)

  const sortedIncidents = useMemo(() => {
    const rows = incidents.data?.incidents ?? []
    const sorted = [...rows].sort((a, b) => {
      const av = a[sortKey] ?? -1
      const bv = b[sortKey] ?? -1
      return sortAsc ? av - bv : bv - av
    })
    return sorted
  }, [incidents.data, sortKey, sortAsc])

  function toggleSort(key: SortKey) {
    if (key === sortKey) setSortAsc(a => !a)
    else { setSortKey(key); setSortAsc(false) }
  }

  function sortHeader(key: SortKey, label: string) {
    const active = key === sortKey
    return (
      <th
        className={`text-right py-2 pr-4 cursor-pointer select-none hover:text-yellow ${active ? 'text-yellow' : ''}`}
        onClick={() => toggleSort(key)}
      >
        {label}{active ? (sortAsc ? ' ▲' : ' ▼') : ''}
      </th>
    )
  }

  if (analytics.loading && !analytics.data) return <div className="text-text-secondary text-sm">Loading…</div>
  if (!analytics.data) return null

  const data = analytics.data
  const maxUtm = Math.max(...data.utm_sources.map(s => s.count), 1)
  const maxGeo = Math.max(...data.geo_breakdown.map(s => s.count), 1)
  const maxRef = Math.max(...(data.referrers ?? []).map(r => r.count), 1)
  const totalTraining = data.training.reduce((s, t) => s + t.count, 0)

  const maxCfCountry  = Math.max(...(cf.data?.countries ?? []).map(c => c.visits), 1)
  const maxCfDevice   = Math.max(...(cf.data?.devices ?? []).map(d => d.visits), 1)
  const maxCfDayVisit = Math.max(...(cf.data?.days ?? []).map(d => d.visits), 1)

  return (
    <div className="space-y-10">
      <div className="flex items-center gap-4">
        <h1 className="font-bold text-yellow text-lg">ANALYTICS</h1>
        <button
          onClick={refreshAll}
          disabled={anyLoading}
          className="px-2 py-1 border border-border text-text-secondary text-xs hover:border-yellow hover:text-yellow transition-colors disabled:opacity-50"
        >
          {anyLoading ? '…' : 'REFRESH'}
        </button>
      </div>

      {/* Cloudflare traffic */}
      <section>
        <h2 className="text-text-secondary text-xs uppercase tracking-widest mb-2">
          Cloudflare traffic (last 7 days)
        </h2>
        <p className="text-text-secondary text-xs leading-relaxed max-w-2xl mb-4">
          Edge/CDN request data from Cloudflare&apos;s GraphQL Analytics API — every request that
          hit the zone, bots included, not a JS beacon. &ldquo;Visits&rdquo; is Cloudflare&apos;s own
          session metric (requests from one client within 30 minutes collapse into one visit). Free-plan
          limits apply: data lags up to 24 hours, and referrer-host breakdown isn&apos;t available on this
          plan (the API rejects that field outright) — for actual referring sites, see &ldquo;Traffic
          sources&rdquo; below, which is first-party.
        </p>

        {cf.error && <div className="text-red text-sm mb-3">{cf.error}</div>}
        {cf.data && cf.data.errors.length > 0 && (
          <div className="text-text-secondary text-xs mb-3">
            {cf.data.errors.length} day(s) had partial data — see server logs.
          </div>
        )}

        {cf.data && (
          <>
            <div className="flex gap-6 mb-4">
              <StatTile value={cf.data.total_visits.toLocaleString()} label="visits" />
              <StatTile value={cf.data.total_requests.toLocaleString()} label="requests" />
            </div>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
              <div>
                <div className="text-text-secondary text-xs uppercase tracking-widest mb-2">By day</div>
                {cf.data.days.map(d => (
                  <BarRow key={d.date} label={d.date.slice(5)} count={d.visits} max={maxCfDayVisit} color="bg-cyan-600" />
                ))}
              </div>
              <div>
                <div className="text-text-secondary text-xs uppercase tracking-widest mb-2">Top countries</div>
                {cf.data.countries.map(c => (
                  <BarRow key={c.country} label={c.country} count={c.visits} max={maxCfCountry} color="bg-purple" />
                ))}
              </div>
              <div>
                <div className="text-text-secondary text-xs uppercase tracking-widest mb-2">Devices</div>
                {cf.data.devices.map(d => (
                  <BarRow key={d.device} label={d.device} count={d.visits} max={maxCfDevice} color="bg-yellow" />
                ))}
              </div>
            </div>
          </>
        )}
      </section>

      {/* Site engagement (first-party) */}
      <section>
        <h2 className="text-text-secondary text-xs uppercase tracking-widest mb-2">
          Site engagement (last {incidents.data?.window_days ?? 30} days)
        </h2>
        <p className="text-text-secondary text-xs leading-relaxed max-w-2xl mb-4">
          First-party, fires on every real pageview (not just UTM-tagged arrivals). A &ldquo;bounce&rdquo;
          is a session that viewed exactly one page anywhere on the site, then left. Dwell time is
          measured from page load to tab-hide or navigation, so it degrades gracefully to &ldquo;no
          value&rdquo; rather than a wrong one when a beacon never fires (crash, force-quit).
        </p>
        {incidents.error && <div className="text-red text-sm mb-3">{incidents.error}</div>}
        {incidents.data && (
          <div className="flex gap-6 flex-wrap">
            <StatTile value={incidents.data.site.sessions.toLocaleString()} label="sessions" />
            <StatTile value={incidents.data.site.pageviews.toLocaleString()} label="pageviews" />
            <StatTile value={pct(incidents.data.site.bounce_rate)} label="bounce rate"
              color={incidents.data.site.bounce_rate !== null && incidents.data.site.bounce_rate > 0.7 ? 'text-red' : undefined} />
            <StatTile value={fmtDwell(incidents.data.site.avg_dwell_ms)} label="avg dwell" />
            <StatTile value={incidents.data.site.shares.toLocaleString()} label="shares" />
            <StatTile value={incidents.data.feed_sessions.toLocaleString()} label="feed/map sessions" />
          </div>
        )}
      </section>

      {/* Incident leaderboard */}
      <section>
        <h2 className="text-text-secondary text-xs uppercase tracking-widest mb-2">
          Incident leaderboard (last {incidents.data?.window_days ?? 30} days)
        </h2>
        <p className="text-text-secondary text-xs leading-relaxed max-w-2xl mb-4">
          Click a column to sort. &ldquo;7d&rdquo; is always the trailing week regardless of the window
          above — that&apos;s the trending signal. CTR is an approximation (arrivals whose referrer was
          the feed or map, over all feed/map sessions in the window), not true impression tracking.
        </p>
        {incidents.data && incidents.data.incidents.length === 0 && (
          <div className="text-text-secondary text-sm">No tracked pageviews yet.</div>
        )}
        {incidents.data && incidents.data.incidents.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm border-collapse">
              <thead>
                <tr className="border-b border-border text-text-secondary text-xs uppercase tracking-widest">
                  <th className="text-left py-2 pr-4">Incident</th>
                  {sortHeader('views_total', 'Views')}
                  {sortHeader('views_7d', '7d')}
                  {sortHeader('shares', 'Shares')}
                  {sortHeader('bounce_rate', 'Bounce')}
                  {sortHeader('avg_dwell_ms', 'Dwell')}
                  {sortHeader('ctr_from_feed', 'CTR')}
                </tr>
              </thead>
              <tbody>
                {sortedIncidents.map(inc => (
                  <tr key={inc.id} className="border-b border-border hover:bg-surface transition-colors">
                    <td className="py-2 pr-4 text-text-primary">
                      <span className={CLASS_COLORS[inc.classification] ?? 'text-text-secondary'}>
                        {CLASS_EMOJI[inc.classification] ?? ''}
                      </span>{' '}
                      {inc.title}
                    </td>
                    <td className="py-2 pr-4 text-right text-text-primary font-bold">{inc.views_total}</td>
                    <td className="py-2 pr-4 text-right text-text-secondary">{inc.views_7d}</td>
                    <td className="py-2 pr-4 text-right text-text-secondary">{inc.shares}</td>
                    <td className="py-2 pr-4 text-right text-text-secondary">{pct(inc.bounce_rate)}</td>
                    <td className="py-2 pr-4 text-right text-text-secondary">{fmtDwell(inc.avg_dwell_ms)}</td>
                    <td className="py-2 pr-4 text-right text-text-secondary">{pct(inc.ctr_from_feed)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* Traffic sources */}
      <section>
        <h2 className="text-text-secondary text-xs uppercase tracking-widest mb-2">Traffic sources</h2>
        <p className="text-text-secondary text-xs leading-relaxed max-w-2xl mb-4">
          UTM breakdown and referrers are first-party (only UTM-tagged link clicks — organic browsing
          without a tag is invisible here). Geo breakdown below is the same first-party data; Cloudflare&apos;s
          country breakdown above is the closer-to-complete picture since it doesn&apos;t depend on a tagged link.
        </p>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
          <div>
            <div className="text-text-secondary text-xs uppercase tracking-widest mb-2">UTM source</div>
            {data.utm_sources.length === 0
              ? <div className="text-text-secondary text-sm">No UTM data yet.</div>
              : data.utm_sources.map(s => (
                  <BarRow key={s.source} label={s.source} count={s.count} max={maxUtm} color="bg-yellow" />
                ))
            }
          </div>
          <div>
            <div className="text-text-secondary text-xs uppercase tracking-widest mb-2">Referrers (top 10)</div>
            {(!data.referrers || data.referrers.length === 0)
              ? <div className="text-text-secondary text-sm">No referrer data yet.</div>
              : data.referrers.map(r => (
                  <BarRow key={r.domain} label={r.domain} count={r.count} max={maxRef} color="bg-cyan-600" />
                ))
            }
          </div>
          <div>
            <div className="text-text-secondary text-xs uppercase tracking-widest mb-2">Geo (top 10)</div>
            {data.geo_breakdown.length === 0
              ? <div className="text-text-secondary text-sm">No geo data yet.</div>
              : data.geo_breakdown.map(g => (
                  <BarRow key={g.country} label={g.country ?? 'Unknown'} count={g.count} max={maxGeo} color="bg-purple" />
                ))
            }
          </div>
        </div>
      </section>

      {/* Queue snapshot + operator actions */}
      <section>
        <h2 className="text-text-secondary text-xs uppercase tracking-widest mb-4">
          Queue snapshot &amp; operator actions ({totalTraining} total)
        </h2>
        <div className="flex gap-6 flex-wrap">
          {Object.entries(data.queue_stats).map(([status, count]) => (
            <StatTile key={status} value={String(count)} label={status} />
          ))}
          {data.training.map(t => {
            const color = t.action === 'reject'
              ? 'text-red' : t.action === 'edit_approve'
              ? 'text-yellow' : 'text-green'
            return (
              <StatTile key={t.action} value={String(t.count)} label={t.action.replace('_', ' ')} color={color} />
            )
          })}
        </div>
      </section>

      {/* Agent autonomy graduation */}
      <section>
        <div className="flex items-center gap-4 mb-4">
          <h2 className="text-text-secondary text-xs uppercase tracking-widest">
            Agent autonomy graduation
          </h2>
          <button
            onClick={refreshAutonomy}
            disabled={autonomy.loading}
            className="px-2 py-1 border border-border text-text-secondary text-xs hover:border-yellow hover:text-yellow transition-colors disabled:opacity-50"
          >
            {autonomy.loading ? '…' : 'REFRESH'}
          </button>
        </div>

        {autonomy.error && (
          <div className="text-red text-sm mb-3">{autonomy.error}</div>
        )}

        {autonomy.data && (() => {
          const entries   = Object.entries(autonomy.data!) as [string, AutonomyCategory][]
          const gradCount = entries.filter(([, d]) => d.graduated).length
          return (
            <>
              <div className="text-text-secondary text-sm mb-4">
                Overall readiness:{' '}
                <span className="text-text-primary font-bold">
                  {gradCount}/{entries.length} categories graduated
                </span>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-sm border-collapse">
                  <thead>
                    <tr className="border-b border-border text-text-secondary text-xs uppercase tracking-widest">
                      <th className="text-left py-2 pr-4">Category</th>
                      <th className="text-left py-2 pr-4">Status</th>
                      <th className="text-right py-2 pr-4">Error rate</th>
                      <th className="text-right py-2 pr-4">Samples</th>
                      <th className="text-left py-2">Unlocks</th>
                    </tr>
                  </thead>
                  <tbody>
                    {entries.map(([signal, d]) => {
                      const badge = STATUS_BADGE[d.status]
                      return (
                        <tr key={signal} className="border-b border-border">
                          <td className="py-2 pr-4 text-text-primary font-bold text-xs">
                            {signal}
                          </td>
                          <td className="py-2 pr-4">
                            <span className={`font-bold text-xs ${badge.color}`}>
                              {badge.icon} {badge.label}
                            </span>
                          </td>
                          <td className="py-2 pr-4 text-right text-text-secondary text-xs">
                            {d.total_decisions > 0
                              ? `${(d.error_rate * 100).toFixed(1)}%`
                              : '—'}
                          </td>
                          <td className="py-2 pr-4 text-right text-text-secondary text-xs">
                            {d.samples_needed > 0
                              ? `${d.total_decisions} / ${d.total_decisions + d.samples_needed} needed`
                              : `${d.total_decisions}`}
                          </td>
                          <td className="py-2 text-text-secondary text-xs">
                            {AUTONOMY_UNLOCKS[signal] ?? '—'}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            </>
          )
        })()}

        {!autonomy.data && !autonomy.loading && !autonomy.error && (
          <div className="text-text-secondary text-sm">No data.</div>
        )}
      </section>
    </div>
  )
}
