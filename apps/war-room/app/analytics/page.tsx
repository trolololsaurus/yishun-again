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

interface CloudflarePoint { t: string; visits: number; requests: number }

interface CloudflareData {
  window:        '24h' | '7d'
  granularity:   'hour' | 'day'
  points:        CloudflarePoint[]
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

// Native SVG — no charting dependency. viewBox scales to any container width;
// a <title> per point gives a native hover tooltip with no JS.
function LineChart({
  points, formatLabel, height = 140, color = '#F1C40F',
}: {
  points: { t: string; value: number }[]
  formatLabel: (t: string) => string
  height?: number
  color?: string
}) {
  if (points.length === 0) return <div className="text-text-secondary text-sm">No data yet.</div>

  const width  = 700
  const padTop = 12
  const max    = Math.max(...points.map(p => p.value), 1)
  const stepX  = points.length > 1 ? width / (points.length - 1) : 0
  const scaleY = (v: number) => padTop + (1 - v / max) * (height - padTop * 2)

  const coords = points.map((p, i) => ({ x: i * stepX, y: scaleY(p.value) }))
  const linePath = coords.map((c, i) => `${i === 0 ? 'M' : 'L'}${c.x.toFixed(1)},${c.y.toFixed(1)}`).join(' ')
  const areaPath = `${linePath} L${coords[coords.length - 1].x.toFixed(1)},${height} L0,${height} Z`

  const peakIdx = points.reduce((best, p, i) => (p.value > points[best].value ? i : best), 0)

  return (
    <div>
      <svg viewBox={`0 0 ${width} ${height}`} className="w-full" style={{ height }} preserveAspectRatio="none">
        <path d={areaPath} fill={color} fillOpacity={0.12} stroke="none" />
        <path d={linePath} fill="none" stroke={color} strokeWidth={2} vectorEffect="non-scaling-stroke" />
        {coords.map((c, i) => (
          <circle
            key={points[i].t}
            cx={c.x} cy={c.y}
            r={i === peakIdx ? 4 : 2.5}
            fill={i === peakIdx ? color : 'currentColor'}
            className={i === peakIdx ? '' : 'text-text-secondary'}
          >
            <title>{`${formatLabel(points[i].t)}: ${points[i].value.toLocaleString()} visits`}</title>
          </circle>
        ))}
      </svg>
      <div className="flex justify-between text-text-secondary text-xs mt-1">
        <span>{formatLabel(points[0].t)}</span>
        <span className="text-yellow">▲ peak {formatLabel(points[peakIdx].t)} ({points[peakIdx].value.toLocaleString()})</span>
        <span>{formatLabel(points[points.length - 1].t)}</span>
      </div>
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

function fmtCfLabel(iso: string, granularity: 'hour' | 'day'): string {
  const d = new Date(iso)
  return granularity === 'hour'
    ? d.toLocaleTimeString('en-SG', { hour: '2-digit', minute: '2-digit', hour12: false })
    : d.toLocaleDateString('en-SG', { day: '2-digit', month: 'short' })
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

// '30d' deliberately absent — this zone's Cloudflare plan caps retention on
// this dataset at ~8 days total (confirmed live), not just 1 day per query.
const CF_WINDOWS = ['24h', '7d'] as const
type CfWindow = typeof CF_WINDOWS[number]

export default function AnalyticsPage() {
  const analytics = useFetch<AnalyticsData>('/api/analytics')
  const incidents  = useFetch<IncidentsData>('/api/analytics/incidents?days=30')
  const autonomy   = useFetch<Record<string, AutonomyCategory>>('/api/autonomy')

  const [cfWindow, setCfWindow] = useState<CfWindow>('7d')
  const cf = useFetch<CloudflareData>(`/api/analytics/cloudflare?window=${cfWindow}`)

  const refreshAutonomy = useCallback(() => {
    autonomy.setLoading(true)
    autonomy.load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    analytics.load()
    incidents.load()
    autonomy.load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Re-fetches on mount too (cf.load's identity is tied to the window-scoped
  // URL), so this alone covers both the initial load and window switches —
  // no separate mount-time cf.load() call needed above.
  useEffect(() => {
    cf.load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cfWindow])

  // Deliberately NOT wrapped with empty deps: cf.load's identity is tied to
  // the window-scoped URL (see useFetch), so a stale empty-deps closure here
  // would keep refreshing whatever window was selected on first render,
  // ignoring later window switches.
  const refreshAll = () => {
    analytics.setLoading(true); analytics.load()
    cf.setLoading(true);        cf.load()
    incidents.setLoading(true); incidents.load()
  }

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

  const maxCfCountry = Math.max(...(cf.data?.countries ?? []).map(c => c.visits), 1)
  const maxCfDevice  = Math.max(...(cf.data?.devices ?? []).map(d => d.visits), 1)

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
        <div className="flex items-center gap-4 mb-2 flex-wrap">
          <h2 className="text-text-secondary text-xs uppercase tracking-widest">
            Cloudflare traffic
          </h2>
          <div className="flex gap-1">
            {CF_WINDOWS.map(w => (
              <button
                key={w}
                onClick={() => setCfWindow(w)}
                className={`px-2 py-1 border text-xs uppercase transition-colors ${
                  w === cfWindow
                    ? 'border-yellow text-yellow'
                    : 'border-border text-text-secondary hover:border-yellow hover:text-yellow'
                }`}
              >
                {w}
              </button>
            ))}
          </div>
        </div>
        <p className="text-text-secondary text-xs mb-4">
          Cloudflare edge traffic, bots included. No bot split or unique visitors on this plan.
        </p>

        {cf.error && <div className="text-red text-sm mb-3">{cf.error}</div>}
        {cf.data && cf.data.errors.length > 0 && (
          <div className="text-text-secondary text-xs mb-3">
            {cf.data.errors.length} interval(s) had partial data — see server logs.
          </div>
        )}

        {cf.data && (
          <>
            <div className="grid grid-cols-3 gap-3 mb-4">
              <StatTile value={cf.data.total_visits.toLocaleString()} label="visits" />
              <StatTile value={cf.data.total_requests.toLocaleString()} label="requests" />
              <StatTile
                value={cf.data.points.length > 0
                  ? Math.max(...cf.data.points.map(p => p.visits)).toLocaleString()
                  : '—'}
                label={`peak / ${cf.data.granularity}`}
              />
            </div>

            <div className="mb-8">
              <div className="text-text-secondary text-xs uppercase tracking-widest mb-2">
                Visits over time
              </div>
              <LineChart
                points={cf.data.points.map(p => ({ t: p.t, value: p.visits }))}
                formatLabel={t => fmtCfLabel(t, cf.data!.granularity)}
              />
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
              <div>
                <div className="text-text-secondary text-xs uppercase tracking-widest mb-2">
                  Countries (top 10)
                </div>
                {cf.data.countries.length === 0
                  ? <div className="text-text-secondary text-sm">No country data yet.</div>
                  : cf.data.countries.map(c => (
                      <BarRow key={c.country} label={c.country} count={c.visits} max={maxCfCountry} color="bg-purple" />
                    ))
                }
              </div>
              <div>
                <div className="text-text-secondary text-xs uppercase tracking-widest mb-2">Devices</div>
                {cf.data.devices.length === 0
                  ? <div className="text-text-secondary text-sm">No device data yet.</div>
                  : cf.data.devices.map(d => (
                      <BarRow key={d.device} label={d.device} count={d.visits} max={maxCfDevice} color="bg-yellow" />
                    ))
                }
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
        <p className="text-text-secondary text-xs mb-4">
          First-party pageviews. Bounce = 1-page session.
        </p>
        {incidents.error && <div className="text-red text-sm mb-3">{incidents.error}</div>}
        {incidents.data && (
          <div className="grid grid-cols-3 sm:grid-cols-6 gap-3">
            <StatTile value={incidents.data.site.sessions.toLocaleString()} label="sessions" />
            <StatTile value={incidents.data.site.pageviews.toLocaleString()} label="pageviews" />
            <StatTile value={pct(incidents.data.site.bounce_rate)} label="bounce rate"
              color={incidents.data.site.bounce_rate !== null && incidents.data.site.bounce_rate > 0.7 ? 'text-red' : undefined} />
            <StatTile value={fmtDwell(incidents.data.site.avg_dwell_ms)} label="avg dwell" />
            <StatTile value={incidents.data.site.shares.toLocaleString()} label="shares" />
            <StatTile value={incidents.data.feed_sessions.toLocaleString()} label="feed/map" />
          </div>
        )}
      </section>

      {/* Incident leaderboard */}
      <section>
        <h2 className="text-text-secondary text-xs uppercase tracking-widest mb-2">
          Incident leaderboard (last {incidents.data?.window_days ?? 30} days)
        </h2>
        <p className="text-text-secondary text-xs mb-4">
          Click a column to sort. CTR = arrivals from feed/map ÷ feed/map sessions.
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
        <p className="text-text-secondary text-xs mb-4">
          First-party: UTM-tagged clicks only.
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
        <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-6 gap-3">
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
