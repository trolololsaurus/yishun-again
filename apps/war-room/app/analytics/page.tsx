'use client'

import { useEffect, useState, useCallback } from 'react'

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
  top_incidents: { id: string; title: string; slug: string; classification: string; views: number }[]
  geo_breakdown: { country: string; count: number }[]
  referrers:     { domain: string; count: number }[]
  total_events:  number
  training:      { action: string; count: number }[]
  queue_stats:   { pending: number; approved: number; rejected: number }
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

export default function AnalyticsPage() {
  const [data,    setData]    = useState<AnalyticsData | null>(null)
  const [loading, setLoading] = useState(true)

  const [autonomy,        setAutonomy]        = useState<Record<string, AutonomyCategory> | null>(null)
  const [autonomyLoading, setAutonomyLoading] = useState(true)
  const [autonomyError,   setAutonomyError]   = useState<string | null>(null)

  const loadAutonomy = useCallback(() => {
    fetch('/api/autonomy')
      .then(r => r.ok ? r.json() : Promise.reject(r.statusText))
      .then(d  => { setAutonomy(d); setAutonomyError(null); setAutonomyLoading(false) })
      .catch(e => { setAutonomyError(String(e)); setAutonomyLoading(false) })
  }, [])

  const refreshAutonomy = useCallback(() => {
    setAutonomyLoading(true)
    setAutonomyError(null)
    loadAutonomy()
  }, [loadAutonomy])

  useEffect(() => {
    fetch('/api/analytics')
      .then(r => r.json())
      .then(d => { setData(d); setLoading(false) })
    loadAutonomy()
  }, [loadAutonomy])

  if (loading) return <div className="text-text-secondary text-sm">Loading…</div>
  if (!data)   return null

  const maxUtm = Math.max(...data.utm_sources.map(s => s.count), 1)
  const maxGeo = Math.max(...data.geo_breakdown.map(s => s.count), 1)
  const maxRef = Math.max(...(data.referrers ?? []).map(r => r.count), 1)
  const totalTraining = data.training.reduce((s, t) => s + t.count, 0)

  return (
    <div className="space-y-10">
      <h1 className="font-bold text-yellow text-lg">ANALYTICS</h1>

      {/* Traffic overview */}
      <section>
        <h2 className="text-text-secondary text-xs uppercase tracking-widest mb-4">Traffic overview</h2>
        <div className="flex gap-6 mb-3">
          <div className="bg-surface border border-border px-6 py-4 text-center">
            <div className="font-bold text-2xl text-text-primary">{data.total_events.toLocaleString()}</div>
            <div className="text-text-secondary text-sm mt-1">tracked visits</div>
          </div>
          {Object.entries(data.queue_stats).map(([status, count]) => (
            <div key={status} className="bg-surface border border-border px-6 py-4 text-center">
              <div className="font-bold text-2xl text-text-primary">{count}</div>
              <div className="text-text-secondary text-sm mt-1">{status}</div>
            </div>
          ))}
        </div>
        <p className="text-text-secondary text-xs leading-relaxed max-w-xl">
          Tracked visits are client-side JS events — only humans who clicked a UTM-tagged
          link appear here. Bots and crawlers do not execute JS and are invisible to this
          counter. For bot traffic, check Vercel Analytics or Cloudflare dashboard.
        </p>
      </section>

      {/* UTM source breakdown */}
      <section>
        <h2 className="text-text-secondary text-xs uppercase tracking-widest mb-4">
          UTM source breakdown
        </h2>
        {data.utm_sources.length === 0
          ? <div className="text-text-secondary text-sm">No UTM data yet.</div>
          : data.utm_sources.map(s => (
              <BarRow key={s.source} label={s.source} count={s.count} max={maxUtm} color="bg-yellow" />
            ))
        }
      </section>

      {/* Referrer breakdown */}
      {data.referrers && data.referrers.length > 0 && (
        <section>
          <h2 className="text-text-secondary text-xs uppercase tracking-widest mb-4">
            Referrers (top 10)
          </h2>
          {data.referrers.map(r => (
            <BarRow key={r.domain} label={r.domain} count={r.count} max={maxRef} color="bg-cyan-600" />
          ))}
        </section>
      )}

      {/* Operator training signals */}
      <section>
        <h2 className="text-text-secondary text-xs uppercase tracking-widest mb-4">
          Operator actions ({totalTraining} total)
        </h2>
        <div className="flex gap-6">
          {data.training.map(t => {
            const color = t.action === 'reject'
              ? 'text-red' : t.action === 'edit_approve'
              ? 'text-yellow' : 'text-green'
            return (
              <div key={t.action} className="bg-surface border border-border px-6 py-4 text-center">
                <div className={`font-bold text-2xl ${color}`}>{t.count}</div>
                <div className="text-text-secondary text-sm mt-1">{t.action.replace('_', ' ')}</div>
              </div>
            )
          })}
        </div>
      </section>

      {/* Geo breakdown */}
      <section>
        <h2 className="text-text-secondary text-xs uppercase tracking-widest mb-4">
          Geo breakdown (top 10)
        </h2>
        {data.geo_breakdown.length === 0
          ? <div className="text-text-secondary text-sm">No geo data yet.</div>
          : data.geo_breakdown.map(g => (
              <BarRow key={g.country} label={g.country ?? 'Unknown'} count={g.count} max={maxGeo} color="bg-purple" />
            ))
        }
      </section>

      {/* Top incidents by views */}
      <section>
        <h2 className="text-text-secondary text-xs uppercase tracking-widest mb-4">
          Top incidents by tracked visits
        </h2>
        {data.top_incidents.length === 0
          ? <div className="text-text-secondary text-sm">No visit data yet.</div>
          : (
            <table className="w-full text-sm border-collapse">
              <thead>
                <tr className="border-b border-border text-text-secondary text-xs uppercase tracking-widest">
                  <th className="text-left py-2 pr-4">#</th>
                  <th className="text-left py-2 pr-4">Incident</th>
                  <th className="text-left py-2 pr-4">Type</th>
                  <th className="text-right py-2">Visits</th>
                </tr>
              </thead>
              <tbody>
                {data.top_incidents.map((inc, i) => (
                  <tr key={inc.id ?? i} className="border-b border-border hover:bg-surface transition-colors">
                    <td className="py-2 pr-4 text-text-secondary">{i + 1}</td>
                    <td className="py-2 pr-4 text-text-primary">{inc.title}</td>
                    <td className={`py-2 pr-4 ${CLASS_COLORS[inc.classification] ?? 'text-text-secondary'}`}>
                      {CLASS_EMOJI[inc.classification] ?? ''} {inc.classification}
                    </td>
                    <td className="py-2 text-right text-text-primary font-bold">{inc.views}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )
        }
      </section>

      {/* Agent autonomy graduation */}
      <section>
        <div className="flex items-center gap-4 mb-4">
          <h2 className="text-text-secondary text-xs uppercase tracking-widest">
            Agent autonomy graduation
          </h2>
          <button
            onClick={refreshAutonomy}
            disabled={autonomyLoading}
            className="px-2 py-1 border border-border text-text-secondary text-xs hover:border-yellow hover:text-yellow transition-colors disabled:opacity-50"
          >
            {autonomyLoading ? '…' : 'REFRESH'}
          </button>
        </div>

        {autonomyError && (
          <div className="text-red text-sm mb-3">{autonomyError}</div>
        )}

        {autonomy && (() => {
          const entries   = Object.entries(autonomy) as [string, AutonomyCategory][]
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

        {!autonomy && !autonomyLoading && !autonomyError && (
          <div className="text-text-secondary text-sm">No data.</div>
        )}
      </section>
    </div>
  )
}
