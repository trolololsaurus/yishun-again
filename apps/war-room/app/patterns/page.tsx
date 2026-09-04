'use client'

import { useEffect, useState } from 'react'

interface IncidentInfo { slug: string; title: string; incident_date: string | null }
interface Pattern {
  id: string
  slug: string
  title: string
  published: boolean
  incident_ids: string[]
  auto_added_incident_ids: string[]
  excluded_incident_ids: string[]
  updated_at: string
}

const SITE = process.env.NEXT_PUBLIC_SITE_URL ?? 'https://yishunagain.com'

export default function PatternsPage() {
  const [patterns, setPatterns] = useState<Pattern[]>([])
  const [incidents, setIncidents] = useState<Record<string, IncidentInfo>>({})
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState<string | null>(null)
  const [attachRef, setAttachRef] = useState<Record<string, string>>({})
  const [err, setErr] = useState<string | null>(null)

  function load() {
    return fetch('/api/patterns')
      .then(r => r.json())
      .then(d => { setPatterns(d.patterns ?? []); setIncidents(d.incidents ?? {}); setLoading(false) })
  }
  useEffect(() => { load() }, [])

  async function detach(pid: string, incidentId: string) {
    if (!confirm('Remove this incident from the pattern? It will not be auto-added again.')) return
    setBusy(incidentId); setErr(null)
    const res = await fetch(`/api/patterns/${pid}/detach`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ incident_id: incidentId }),
    })
    if (!res.ok) setErr((await res.json()).error ?? 'Detach failed')
    await load(); setBusy(null)
  }

  async function attach(pid: string) {
    const ref = (attachRef[pid] ?? '').trim()
    if (!ref) return
    setBusy(pid); setErr(null)
    const res = await fetch(`/api/patterns/${pid}/attach`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ incident: ref }),
    })
    if (!res.ok) setErr((await res.json()).error ?? 'Attach failed')
    else setAttachRef(prev => ({ ...prev, [pid]: '' }))
    await load(); setBusy(null)
  }

  if (loading) return <div className="text-text-secondary text-sm">Loading…</div>

  return (
    <div className="max-w-3xl">
      <h1 className="font-display text-yellow text-sm mb-1">PATTERNS</h1>
      <p className="text-text-secondary text-sm mb-6">
        Curated pattern pages. Incidents are hand-picked; the auto-append agent adds high-confidence
        matches (flagged <span className="text-yellow">AUTO</span> below). Removing one excludes it
        from future auto-append.
      </p>
      {err && <div className="text-red text-sm mb-4">{err}</div>}

      <div className="space-y-8">
        {patterns.map(p => {
          const auto = new Set(p.auto_added_incident_ids ?? [])
          const ordered = [...(p.incident_ids ?? [])].sort((a, b) =>
            (incidents[a]?.incident_date ?? '').localeCompare(incidents[b]?.incident_date ?? ''))
          return (
            <section key={p.id} className="border border-border rounded">
              <header className="flex items-center justify-between px-4 py-3 border-b border-border">
                <div>
                  <a href={`${SITE}/patterns/${p.slug}`} target="_blank" rel="noopener noreferrer"
                     className="font-body font-bold text-text-primary hover:text-yellow text-sm">
                    {p.title}
                  </a>
                  <span className="text-text-secondary text-xs ml-2">{p.incident_ids?.length ?? 0} incidents</span>
                </div>
                <span className={`text-xs px-2 py-0.5 rounded ${p.published ? 'text-green border border-green/40' : 'text-text-secondary border border-border'}`}>
                  {p.published ? 'PUBLISHED' : 'DRAFT'}
                </span>
              </header>

              <ul className="divide-y divide-border">
                {ordered.map(id => {
                  const inc = incidents[id]
                  return (
                    <li key={id} className="flex items-center gap-3 px-4 py-2">
                      <div className="min-w-0 flex-1">
                        <div className="text-text-primary text-sm truncate">
                          {inc?.title ?? <span className="text-red">missing incident {id.slice(0, 8)}…</span>}
                        </div>
                        <div className="text-text-secondary text-xs">
                          {inc?.incident_date ?? '—'}
                          {auto.has(id) && <span className="text-yellow ml-2">AUTO — review</span>}
                        </div>
                      </div>
                      <button
                        onClick={() => detach(p.id, id)}
                        disabled={busy === id}
                        className="text-red hover:bg-red/10 border border-red/40 rounded px-2 py-0.5 text-xs disabled:opacity-40"
                      >
                        {busy === id ? '…' : 'Remove'}
                      </button>
                    </li>
                  )
                })}
              </ul>

              <div className="flex gap-2 px-4 py-3 border-t border-border">
                <input
                  value={attachRef[p.id] ?? ''}
                  onChange={e => setAttachRef(prev => ({ ...prev, [p.id]: e.target.value }))}
                  onKeyDown={e => { if (e.key === 'Enter') attach(p.id) }}
                  placeholder="incident slug or UUID to add"
                  className="flex-1 bg-bg border border-border rounded px-2 py-1 text-sm text-text-primary placeholder:text-text-secondary/60"
                />
                <button
                  onClick={() => attach(p.id)}
                  disabled={busy === p.id}
                  className="text-yellow border border-yellow/40 hover:bg-yellow/10 rounded px-3 py-1 text-sm disabled:opacity-40"
                >
                  Add
                </button>
              </div>
            </section>
          )
        })}
      </div>
    </div>
  )
}
