'use client'

import { useState } from 'react'
import type { QueueItem, Classification, RejectReason, AgentRelatedIncident, DismissCategory } from '@/lib/types'
import { DISMISS_CATEGORIES } from '@/lib/types'
import {
  CLASS_ICON, CLASS_COLOR, CLASS_LABEL,
  confidenceColor, confidenceLabel,
  severityDiamonds, hypeMeter,
} from '@/lib/utils'

const REJECT_REASONS: { value: RejectReason; label: string }[] = [
  { value: 'noise',       label: 'Noise — not a real incident' },
  { value: 'duplicate',   label: 'Duplicate — already archived' },
  { value: 'unverified',  label: 'Unverified — source too thin' },
  { value: 'too_thin',    label: 'Too thin — insufficient detail' },
  { value: 'legal_risk',  label: 'Legal risk — do not publish' },
]

interface Props {
  item:            QueueItem
  relatedPreviews: Record<string, { title: string; slug: string }>
  onProcessed:     (id: string) => void
}

export function QueueCard({ item, relatedPreviews, onProcessed }: Props) {
  const rc = item.raw_content as Record<string, unknown>

  // Editable draft state — pre-filled with agent output
  const [title,      setTitle]      = useState(item.proposed_title      ?? '')
  const [summary,    setSummary]    = useState(item.proposed_summary     ?? '')
  const [classif,    setClassif]    = useState<Classification>(
    (item.proposed_classification as Classification) ?? 'dagger'
  )
  const [severity,   setSeverity]   = useState(item.proposed_severity   ?? 3)
  const [pixelPrompt, setPixelPrompt] = useState(
    (rc.pixel_art_prompt as string) ?? item.proposed_pixel_prompt ?? ''
  )

  // Auto-expand source view when confidence < 0.85
  const [showSource,       setShowSource]       = useState((item.agent_confidence ?? 1) < 0.85)
  const [showSourceLinks,  setShowSourceLinks]  = useState(false)
  const [showRejectMenu,   setShowRejectMenu]   = useState(false)
  const [loading,          setLoading]          = useState(false)
  const [error,            setError]            = useState<string | null>(null)

  // Detect if operator changed anything
  const isEdited = (
    title      !== (item.proposed_title      ?? '') ||
    summary    !== (item.proposed_summary    ?? '') ||
    classif    !== (item.proposed_classification ?? 'dagger') ||
    severity   !== (item.proposed_severity   ?? 3)  ||
    pixelPrompt !== ((rc.pixel_art_prompt as string) ?? item.proposed_pixel_prompt ?? '')
  )

  const conf             = item.agent_confidence
  const hype             = (rc.hype_meter as number) ?? 0
  const sourceUrls       = (rc.source_urls as string[]) ?? [item.source_url]
  const rawContent       = (rc.content as string) ?? ''
  const linkValidation   = (rc.link_validation as Record<string, { status: string; status_code: number; final_url: string; wayback_url?: string }>) ?? {}
  const milestoneLabel   = (rc.milestone_label    as string) ?? null
  const triggeredByTitle = (rc.triggered_by_title as string) ?? null
  const triggeredByUrl   = (rc.triggered_by_url   as string) ?? null
  const triggeredDate    = (rc.triggered_date     as string) ?? null

  const agentRelatedInit = (rc.agent_related_incidents as AgentRelatedIncident[]) ?? []
  const [relatedState,    setRelatedState]    = useState<AgentRelatedIncident[]>(agentRelatedInit)
  const [dismissingId,    setDismissingId]    = useState<string | null>(null)
  const [dismissCategory, setDismissCategory] = useState<DismissCategory | ''>('')
  const [dismissDetail,   setDismissDetail]   = useState('')

  function openDismissForm(incidentId: string) {
    setDismissingId(incidentId)
    setDismissCategory('')
    setDismissDetail('')
  }

  function cancelDismiss() {
    setDismissingId(null)
    setDismissCategory('')
    setDismissDetail('')
  }

  async function handleApprove() {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(`/api/queue/${item.id}/approve`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ title, summary, classification: classif, severity, pixel_art_prompt: pixelPrompt }),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.error ?? `HTTP ${res.status}`)
      }
      onProcessed(item.id)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unknown error')
      setLoading(false)
    }
  }

  async function handleReject(reason: RejectReason) {
    setLoading(true)
    setError(null)
    setShowRejectMenu(false)
    try {
      const res = await fetch(`/api/queue/${item.id}/reject`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ reason }),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.error ?? `HTTP ${res.status}`)
      }
      onProcessed(item.id)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unknown error')
      setLoading(false)
    }
  }

  async function handleDismissLink(incidentId: string, category: DismissCategory, detail: string) {
    try {
      const res = await fetch(`/api/queue/${item.id}/dismiss-alert`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({
          related_incident_id: incidentId,
          reason_category:     category,
          reason_detail:       detail || undefined,
        }),
      })
      if (res.ok) {
        setRelatedState(prev => prev.map(r =>
          r.incident_id === incidentId ? { ...r, dismissed: true } : r
        ))
        setDismissingId(null)
      }
    } catch { /* non-fatal */ }
  }

  const visibleRelated = relatedState.filter(r => !r.dismissed)

  return (
    <article className="bg-surface border border-border rounded">
      {/* ── Header bar ───────────────────────────────────── */}
      <div className="flex items-center gap-3 px-4 py-3 border-b border-border">
        <span className={`text-lg ${CLASS_COLOR[classif] ?? ''}`}>
          {CLASS_ICON[classif]}
        </span>
        <span className={`font-body font-bold text-sm ${CLASS_COLOR[classif] ?? ''}`}>
          {CLASS_LABEL[classif]}
        </span>
        <span className="font-body text-text-secondary text-sm">
          {severityDiamonds(severity)}
        </span>
        {hype > 0 && (
          <span className="font-body text-yellow text-sm" title={`Hype meter ${hype}`}>
            {hypeMeter(hype)}
          </span>
        )}
        <span className={`ml-2 px-2 py-0.5 rounded font-body text-sm font-bold ${confidenceColor(conf)}`}>
          {confidenceLabel(conf)}
        </span>
        {milestoneLabel && (
          <span className="px-2 py-0.5 border border-yellow text-yellow font-body font-bold uppercase"
                style={{ fontSize: '10px' }}>
            &#x26A1; {milestoneLabel}
          </span>
        )}
        <span className="ml-auto font-body text-text-secondary text-sm">
          {new Date(item.created_at).toLocaleString('en-SG')}
        </span>
        <button
          onClick={() => setShowSource(s => !s)}
          className="px-2 py-1 border border-border text-text-secondary font-body text-sm hover:border-yellow hover:text-yellow transition-colors"
        >
          {showSource ? 'Hide Source' : 'View Source'}
        </button>
      </div>

      {/* ── Body ─────────────────────────────────────────── */}
      <div className={showSource ? 'grid grid-cols-2 divide-x divide-border' : ''}>

        {/* Raw source pane (read-only, only when expanded) */}
        {showSource && (
          <div className="p-4 overflow-auto max-h-[600px]">
            <div className="font-body text-text-secondary text-sm mb-2 uppercase tracking-widest">
              Raw Source
            </div>
            <a
              href={item.source_url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-yellow text-sm font-body hover:underline break-all"
            >
              {item.source_url}
            </a>
            <div className="mt-3 font-body text-sm text-text-secondary whitespace-pre-wrap leading-relaxed">
              {rawContent || '(No raw content stored)'}
            </div>
          </div>
        )}

        {/* Draft edit pane */}
        <div className="p-4 space-y-4">
          {/* Milestone "triggered by" context — milestone posts only */}
          {triggeredByTitle && (
            <div className="border border-yellow/40 bg-yellow/5 p-3">
              <div className="font-body text-yellow font-bold mb-2 uppercase" style={{ fontSize: '10px' }}>
                Triggered by
              </div>
              <div className="font-body text-text-primary text-sm">
                {triggeredByTitle}
              </div>
              {triggeredByUrl && (
                <a
                  href={triggeredByUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-yellow font-body hover:underline break-all"
                  style={{ fontSize: '11px' }}
                >
                  {triggeredByUrl}
                </a>
              )}
              {triggeredDate && (
                <div className="font-body text-text-secondary mt-1" style={{ fontSize: '10px' }}>
                  {triggeredDate}
                </div>
              )}
            </div>
          )}

          {/* Title */}
          <div>
            <label className="font-body text-text-secondary text-sm uppercase tracking-widest block mb-1">
              Title <span className="text-border">{title.length}/120</span>
            </label>
            <input
              type="text"
              value={title}
              maxLength={120}
              onChange={e => setTitle(e.target.value)}
              className="w-full px-3 py-2 bg-bg border border-border text-text-primary font-body text-base rounded focus:border-yellow focus:outline-none"
            />
          </div>

          {/* Summary */}
          <div>
            <label className="font-body text-text-secondary text-sm uppercase tracking-widest block mb-1">
              Summary <span className={summary.length < 500 || summary.length > 800 ? 'text-red' : 'text-green'}>
                {summary.length} chars
              </span>
            </label>
            <textarea
              value={summary}
              onChange={e => setSummary(e.target.value)}
              rows={6}
              className="w-full px-3 py-2 bg-bg border border-border text-text-primary font-body text-base rounded focus:border-yellow focus:outline-none resize-y"
            />
          </div>

          {/* Classification */}
          <div>
            <label className="font-body text-text-secondary text-sm uppercase tracking-widest block mb-2">
              Classification
            </label>
            <div className="flex gap-2">
              {(['dagger', 'clown', 'heart'] as Classification[]).map(c => (
                <button
                  key={c}
                  onClick={() => setClassif(c)}
                  className={[
                    'px-3 py-1.5 border font-body text-sm rounded transition-colors',
                    classif === c
                      ? `border-current ${CLASS_COLOR[c]}`
                      : 'border-border text-text-secondary hover:border-text-secondary',
                  ].join(' ')}
                >
                  {CLASS_ICON[c]} {CLASS_LABEL[c]}
                </button>
              ))}
            </div>
          </div>

          {/* Severity */}
          <div>
            <label className="font-body text-text-secondary text-sm uppercase tracking-widest block mb-2">
              Severity
            </label>
            <div className="flex gap-2">
              {[1, 2, 3, 4, 5].map(n => (
                <button
                  key={n}
                  onClick={() => setSeverity(n)}
                  className={[
                    'w-8 h-8 border font-body text-sm rounded transition-colors',
                    severity === n
                      ? 'border-yellow text-yellow bg-yellow/10'
                      : 'border-border text-text-secondary hover:border-text-secondary',
                  ].join(' ')}
                >
                  {n}
                </button>
              ))}
            </div>
          </div>

          {/* Pixel art prompt */}
          <div>
            <label className="font-body text-text-secondary text-sm uppercase tracking-widest block mb-1">
              Pixel Art Prompt
            </label>
            <textarea
              value={pixelPrompt}
              onChange={e => setPixelPrompt(e.target.value)}
              rows={2}
              className="w-full px-3 py-2 bg-bg border border-border text-text-primary font-body text-base rounded focus:border-yellow focus:outline-none resize-y"
            />
          </div>

          {/* Possible related banners — pending items only (no confirm-link without a matched incident) */}
          {visibleRelated.length > 0 && (
            <div className="space-y-2">
              {visibleRelated.map(link => {
                const preview = relatedPreviews[link.incident_id]
                const isOpen  = dismissingId === link.incident_id
                return (
                  <div key={link.incident_id}
                       className="border border-yellow/30 bg-yellow/5 p-3 space-y-2">
                    <div className="flex items-center gap-2">
                      <span className="font-body text-yellow font-bold uppercase" style={{ fontSize: '10px' }}>
                        POSSIBLE {link.link_type.replace('_', ' ').toUpperCase()}
                      </span>
                      <span className="font-body text-text-secondary" style={{ fontSize: '11px' }}>
                        conf: {(link.confidence * 100).toFixed(0)}%
                      </span>
                    </div>
                    <div className="font-body text-text-primary text-sm">
                      {preview?.title ?? link.incident_id}
                    </div>
                    <div className="font-body text-text-secondary" style={{ fontSize: '11px' }}>
                      {link.reason}
                    </div>

                    {isOpen ? (
                      <div className="space-y-2 pt-1 border-t border-yellow/20">
                        <div>
                          <label className="font-body text-text-secondary uppercase tracking-widest block mb-1"
                                 style={{ fontSize: '10px' }}>
                            Dismiss reason:
                          </label>
                          <select
                            value={dismissCategory}
                            onChange={e => setDismissCategory(e.target.value as DismissCategory)}
                            className="w-full px-2 py-1.5 bg-bg border border-border text-text-primary font-body text-sm rounded focus:border-yellow focus:outline-none"
                          >
                            <option value="">— select —</option>
                            {(Object.entries(DISMISS_CATEGORIES) as [DismissCategory, typeof DISMISS_CATEGORIES[DismissCategory]][]).map(([key, cat]) => (
                              <option key={key} value={key}>{cat.label}</option>
                            ))}
                          </select>
                          {dismissCategory && (
                            <div className="font-body text-text-secondary mt-1" style={{ fontSize: '10px' }}>
                              {DISMISS_CATEGORIES[dismissCategory].description}
                            </div>
                          )}
                        </div>
                        <div>
                          <label className="font-body text-text-secondary uppercase tracking-widest block mb-1"
                                 style={{ fontSize: '10px' }}>
                            Additional detail (optional):
                          </label>
                          <input
                            type="text"
                            value={dismissDetail}
                            maxLength={200}
                            onChange={e => setDismissDetail(e.target.value)}
                            placeholder="Free text, max 200 chars"
                            className="w-full px-2 py-1.5 bg-bg border border-border text-text-primary font-body text-sm rounded focus:border-yellow focus:outline-none"
                          />
                        </div>
                        <div className="flex gap-2">
                          <button
                            disabled={!dismissCategory}
                            onClick={() => handleDismissLink(link.incident_id, dismissCategory as DismissCategory, dismissDetail)}
                            className="px-3 py-1 border border-red text-red font-body font-bold hover:bg-red hover:text-bg transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                            style={{ fontSize: '11px' }}
                          >
                            CONFIRM DISMISS
                          </button>
                          <button
                            onClick={cancelDismiss}
                            className="px-3 py-1 border border-border text-text-secondary font-body hover:border-text-secondary transition-colors"
                            style={{ fontSize: '11px' }}
                          >
                            CANCEL
                          </button>
                        </div>
                      </div>
                    ) : (
                      <button
                        onClick={() => openDismissForm(link.incident_id)}
                        className="font-body text-text-secondary hover:text-red transition-colors"
                        style={{ fontSize: '11px' }}
                      >
                        DISMISS
                      </button>
                    )}
                  </div>
                )
              })}
            </div>
          )}

          {/* Corroboration + buzz */}
          <div className="flex gap-6 font-body text-sm">
            <span className="text-text-secondary">
              Corroborated: <span className="text-text-primary">{item.corroboration_count}</span>
            </span>
            {item.edmw_signal_count > 0 && (
              <span className="text-text-secondary">
                Forum buzz: <span className="text-yellow">{item.edmw_signal_count}</span>
              </span>
            )}
          </div>

          {/* Source links with link validation badges */}
          <div>
            <button
              onClick={() => setShowSourceLinks(s => !s)}
              className="font-body text-text-secondary text-sm hover:text-text-primary"
            >
              {showSourceLinks ? '▲' : '▼'} Sources ({sourceUrls.length})
            </button>
            {showSourceLinks && (
              <ul className="mt-2 space-y-2">
                {sourceUrls.map(url => {
                  const val = linkValidation[url]
                  const badge = val
                    ? val.status === 'ok'      ? { icon: '🟢', label: 'Live',    cls: 'text-green'  }
                    : val.status === 'paywall'  ? { icon: '🟡', label: 'Paywall', cls: 'text-yellow' }
                    : val.wayback_url           ? { icon: '🔵', label: 'Wayback', cls: 'text-blue-400' }
                    :                            { icon: '🔴', label: 'Dead',    cls: 'text-red'    }
                    : null
                  return (
                    <li key={url} className="space-y-0.5">
                      <div className="flex items-center gap-2">
                        {badge && (
                          <span
                            className={`font-body text-xs font-bold ${badge.cls}`}
                            title={`HTTP ${val?.status_code ?? 0}`}
                          >
                            {badge.icon} {badge.label}
                          </span>
                        )}
                        <a
                          href={url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className={`font-body text-sm hover:underline break-all ${
                            badge?.label === 'Dead' ? 'text-text-secondary line-through' : 'text-yellow'
                          }`}
                        >
                          {url}
                        </a>
                      </div>
                      {val?.wayback_url && (
                        <div className="pl-14">
                          <a
                            href={val.wayback_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="font-body text-blue-400 text-xs hover:underline break-all"
                          >
                            ↳ Wayback: {val.wayback_url.slice(0, 80)}
                          </a>
                        </div>
                      )}
                    </li>
                  )
                })}
              </ul>
            )}
          </div>
        </div>
      </div>

      {/* ── Action bar ───────────────────────────────────── */}
      <div className="flex items-center gap-3 px-4 py-3 border-t border-border">
        {error && (
          <span className="font-body text-red text-sm">{error}</span>
        )}

        <div className="ml-auto flex items-center gap-2">
          {/* Reject */}
          <div className="relative">
            <button
              onClick={() => setShowRejectMenu(r => !r)}
              disabled={loading}
              className="px-3 py-2 border border-red text-red font-body text-sm hover:bg-red hover:text-bg transition-colors disabled:opacity-50"
            >
              Reject ▾
            </button>
            {showRejectMenu && (
              <div className="absolute bottom-full right-0 mb-1 bg-surface border border-border min-w-48 z-10">
                {REJECT_REASONS.map(r => (
                  <button
                    key={r.value}
                    onClick={() => handleReject(r.value)}
                    className="block w-full text-left px-3 py-2 font-body text-sm text-text-secondary hover:bg-bg hover:text-red"
                  >
                    {r.label}
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* Approve */}
          <button
            onClick={handleApprove}
            disabled={loading}
            className="px-4 py-2 bg-green text-bg font-body text-sm font-bold hover:opacity-90 transition-opacity disabled:opacity-50"
          >
            {loading ? '…' : isEdited ? 'Save & Approve' : 'Approve ✓'}
          </button>
        </div>
      </div>
    </article>
  )
}
