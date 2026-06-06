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
  top_incidents: { id: string; title: string; slug: string; classification: string }[]
  geo_breakdown: { country: string; count: number }[]
  vpn_count:     number
  training:      { action: string; count: number }[]
  queue_stats:   { pending: number; approved: number; rejected: number }
}

function BarRow({ label, count, max, color }: { label: string; count: number; max: number; color: string }) {
  const pct = max > 0 ? Math.round((count / max) * 100) : 0
  return (
    <div className="flex items-center gap-3 mb-2">
      <span className="w-32 text-text-secondary font-body text-sm truncate">{label}</span>
      <div className="flex-1 bg-border/30 h-4 relative">
        <div className={`h-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="w-10 text-right font-body text-sm text-text-primary">{count}</span>
    </div>
  )
}

export default function AnalyticsPage() {
  const [data,    setData]    = useState<AnalyticsData | null>(null)
  const [loading, setLoading] = useState(true)

  const [autonomy,        setAutonomy]        = useState<Record<string, AutonomyCategory> | null>(null)
  const [autonomyLoading, setAutonomyLoading] = useState(true)
  const [autonomyError,   setAutonomyError]   = useState<string | null>(null)

  const loadAutonomy = useCallback(() => {
    setAutonomyLoading(true)
    setAutonomyError(null)
    fetch('/api/autonomy')
      .then(r => r.ok ? r.json() : Promise.reject(r.statusText))
      .then(d  => { setAutonomy(d); setAutonomyLoading(false) })
      .catch(e => { setAutonomyError(String(e)); setAutonomyLoading(false) })
  }, [])

  useEffect(() => {
    fetch('/api/analytics')
      .then(r => r.json())
      .then(d => { setData(d); setLoading(false) })
    loadAutonomy()
  }, [loadAutonomy])

  if (loading) return <div className="font-body text-text-secondary text-sm">Loading…</div>
  if (!data)   return null

  const maxUtm = Math.max(...data.utm_sources.map(s => s.count), 1)
  const maxGeo = Math.max(...data.geo_breakdown.map(s => s.count), 1)
  const totalTraining = data.training.reduce((s, t) => s + t.count, 0)

  return (
    <div className="space-y-10">
      <h1 className="font-body font-bold text-yellow text-lg">ANALYTICS</h1>

      {/* Queue snapshot */}
      <section>
        <h2 className="font-body text-text-secondary text-sm uppercase tracking-widest mb-4">Queue snapshot</h2>
        <div className="flex gap-6">
          {Object.entries(data.queue_stats).map(([status, count]) => (
            <div key={status} className="bg-surface border border-border px-6 py-4 text-center">
              <div className="font-body font-bold text-2xl text-text-primary">{count}</div>
              <div className="font-body text-text-secondary text-sm mt-1">{status}</div>
            </div>
          ))}
          <div className="bg-surface border border-border px-6 py-4 text-center">
            <div className="font-body font-bold text-2xl text-red">{data.vpn_count}</div>
            <div className="font-body text-text-secondary text-sm mt-1">VPN flagged</div>
          </div>
        </div>
      </section>

      {/* UTM source breakdown */}
      <section>
        <h2 className="font-body text-text-secondary text-sm uppercase tracking-widest mb-4">
          UTM source breakdown
        </h2>
        {data.utm_sources.length === 0
          ? <div className="font-body text-text-secondary text-sm">No UTM data yet.</div>
          : data.utm_sources.map(s => (
              <BarRow key={s.source} label={s.source} count={s.count} max={maxUtm} color="bg-yellow" />
            ))
        }
      </section>

      {/* Operator training signals */}
      <section>
        <h2 className="font-body text-text-secondary text-sm uppercase tracking-widest mb-4">
          Operator actions ({totalTraining} total)
        </h2>
        <div className="flex gap-6">
          {data.training.map(t => {
            const color = t.action === 'reject'
              ? 'text-red' : t.action === 'edit_approve'
              ? 'text-yellow' : 'text-green'
            return (
              <div key={t.action} className="bg-surface border border-border px-6 py-4 text-center">
                <div className={`font-body font-bold text-2xl ${color}`}>{t.count}</div>
                <div className="font-body text-text-secondary text-sm mt-1">{t.action}</div>
              </div>
            )
          })}
        </div>
      </section>

      {/* Geo breakdown */}
      <section>
        <h2 className="font-body text-text-secondary text-sm uppercase tracking-widest mb-4">
          Geo breakdown (top 10)
        </h2>
        {data.geo_breakdown.length === 0
          ? <div className="font-body text-text-secondary text-sm">No geo data yet.</div>
          : data.geo_breakdown.map(g => (
              <BarRow key={g.country} label={g.country ?? 'Unknown'} count={g.count} max={maxGeo} color="bg-purple" />
            ))
        }
      </section>

      {/* Top incidents */}
      <section>
        <h2 className="font-body text-text-secondary text-sm uppercase tracking-widest mb-4">
          Top incidents by traffic
        </h2>
        {data.top_incidents.length === 0
          ? <div className="font-body text-text-secondary text-sm">No incidents yet.</div>
          : (
            <table className="w-full font-body text-sm border-collapse">
              <thead>
                <tr className="border-b border-border text-text-secondary">
                  <th className="text-left py-2 pr-4">#</th>
                  <th className="text-left py-2 pr-4">Title</th>
                  <th className="text-left py-2">Type</th>
                </tr>
              </thead>
              <tbody>
                {data.top_incidents.map((inc, i) => (
                  <tr key={inc.id ?? i} className="border-b border-border">
                    <td className="py-2 pr-4 text-text-secondary">{i + 1}</td>
                    <td className="py-2 pr-4 text-text-primary">{inc.title}</td>
                    <td className="py-2 text-text-secondary">{inc.classification}</td>
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
          <h2 className="font-body text-text-secondary text-sm uppercase tracking-widest">
            Agent autonomy graduation
          </h2>
          <button
            onClick={loadAutonomy}
            disabled={autonomyLoading}
            className="px-2 py-1 border border-border text-text-secondary font-body hover:border-yellow hover:text-yellow transition-colors disabled:opacity-50"
            style={{ fontSize: '10px' }}
          >
            {autonomyLoading ? '…' : 'REFRESH'}
          </button>
        </div>

        {autonomyError && (
          <div className="font-body text-red text-sm mb-3">{autonomyError}</div>
        )}

        {autonomy && (() => {
          const entries   = Object.entries(autonomy) as [string, AutonomyCategory][]
          const gradCount = entries.filter(([, d]) => d.graduated).length
          return (
            <>
              <div className="font-body text-text-secondary text-sm mb-4">
                Overall readiness:{' '}
                <span className="text-text-primary font-bold">
                  {gradCount}/{entries.length} categories graduated
                </span>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full font-body text-sm border-collapse">
                  <thead>
                    <tr className="border-b border-border text-text-secondary" style={{ fontSize: '10px' }}>
                      <th className="text-left py-2 pr-4 uppercase tracking-widest">Category</th>
                      <th className="text-left py-2 pr-4 uppercase tracking-widest">Status</th>
                      <th className="text-right py-2 pr-4 uppercase tracking-widest">Error rate</th>
                      <th className="text-right py-2 pr-4 uppercase tracking-widest">Samples</th>
                      <th className="text-left py-2 uppercase tracking-widest">Unlocks</th>
                    </tr>
                  </thead>
                  <tbody>
                    {entries.map(([signal, d]) => {
                      const badge = STATUS_BADGE[d.status]
                      return (
                        <tr key={signal} className="border-b border-border">
                          <td className="py-2 pr-4 text-text-primary font-bold" style={{ fontSize: '11px' }}>
                            {signal}
                          </td>
                          <td className="py-2 pr-4">
                            <span className={`font-bold ${badge.color}`} style={{ fontSize: '10px' }}>
                              {badge.icon} {badge.label}
                            </span>
                          </td>
                          <td className="py-2 pr-4 text-right text-text-secondary" style={{ fontSize: '11px' }}>
                            {d.total_decisions > 0
                              ? `${(d.error_rate * 100).toFixed(1)}%`
                              : '—'}
                          </td>
                          <td className="py-2 pr-4 text-right text-text-secondary" style={{ fontSize: '11px' }}>
                            {d.samples_needed > 0
                              ? `${d.total_decisions} / ${d.total_decisions + d.samples_needed} needed`
                              : `${d.total_decisions}`}
                          </td>
                          <td className="py-2 text-text-secondary" style={{ fontSize: '11px' }}>
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
          <div className="font-body text-text-secondary text-sm">No data.</div>
        )}
      </section>
    </div>
  )
}
