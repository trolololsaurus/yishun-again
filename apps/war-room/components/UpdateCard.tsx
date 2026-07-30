'use client'

import { useState } from 'react'
import type { QueueItem, IncidentPreview, AgentRelatedIncident } from '@/lib/types'
import { CLASS_ICON, CLASS_COLOR, CLASS_LABEL, severityDiamonds, confidenceColor, confidenceLabel, safeHref } from '@/lib/utils'

interface Props {
  item:             QueueItem
  targetIncident:   IncidentPreview
  relatedPreviews:  Record<string, { title: string; slug: string }>
  onProcessed:      (id: string) => void
}

export function UpdateCard({ item, targetIncident, relatedPreviews, onProcessed }: Props) {
  const rc = item.raw_content as Record<string, unknown>
  const agentRelated: AgentRelatedIncident[] = (rc.agent_related_incidents as AgentRelatedIncident[]) ?? []

  const [summary,       setSummary]       = useState(item.proposed_summary ?? '')
  const [relatedState,  setRelatedState]  = useState<AgentRelatedIncident[]>(agentRelated)
  const [loading,       setLoading]       = useState(false)
  const [error,         setError]         = useState<string | null>(null)

  const conf     = item.agent_confidence
  const newUrl   = item.source_url
  const headline = item.proposed_title ?? ''

  async function post(endpoint: string, body?: unknown) {
    const res = await fetch(`/api/queue/${item.id}/${endpoint}`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    body ? JSON.stringify(body) : undefined,
    })
    if (!res.ok) {
      const data = await res.json().catch(() => ({}))
      throw new Error(data.error ?? `HTTP ${res.status}`)
    }
    return res.json()
  }

  async function handleConfirmUpdate() {
    setLoading(true); setError(null)
    try {
      await post('confirm-update', { updated_summary: summary.trim() || undefined })
      onProcessed(item.id)
    } catch (e) { setError(e instanceof Error ? e.message : 'Unknown error'); setLoading(false) }
  }

  async function handleRejectUpdate() {
    setLoading(true); setError(null)
    try {
      await post('reject-update')
      onProcessed(item.id)
    } catch (e) { setError(e instanceof Error ? e.message : 'Unknown error'); setLoading(false) }
  }

  async function handleSplit() {
    setLoading(true); setError(null)
    try {
      await post('split')
      onProcessed(item.id)
    } catch (e) { setError(e instanceof Error ? e.message : 'Unknown error'); setLoading(false) }
  }

  async function handleConfirmLink(link: AgentRelatedIncident) {
    setLoading(true); setError(null)
    try {
      await post('confirm-link', {
        related_incident_id: link.incident_id,
        link_type:           link.link_type,
        confidence:          link.confidence,
        agent_reason:        link.reason,
      })
      setRelatedState(prev => prev.filter(r => r.incident_id !== link.incident_id))
    } catch (e) { setError(e instanceof Error ? e.message : 'Unknown error') }
    finally { setLoading(false) }
  }

  async function handleDismissLink(link: AgentRelatedIncident) {
    setLoading(true); setError(null)
    try {
      await post('dismiss-link', { related_incident_id: link.incident_id })
      setRelatedState(prev => prev.map(r =>
        r.incident_id === link.incident_id ? { ...r, dismissed: true } : r
      ))
    } catch (e) { setError(e instanceof Error ? e.message : 'Unknown error') }
    finally { setLoading(false) }
  }

  const visibleRelated = relatedState.filter(r => !r.dismissed)

  return (
    <article className="bg-surface border border-cyan-500/40 rounded">

      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-3 border-b border-cyan-500/40">
        <span className="px-2 py-0.5 bg-cyan-500 text-bg font-body font-bold uppercase"
              style={{ fontSize: '10px' }}>
          NEW UPDATE
        </span>
        <span className={`font-body font-bold text-sm ${CLASS_COLOR[targetIncident.classification] ?? ''}`}>
          {CLASS_ICON[targetIncident.classification]} {CLASS_LABEL[targetIncident.classification]}
        </span>
        <span className="font-body text-text-secondary text-sm">
          {severityDiamonds(targetIncident.severity)}
        </span>
        <span className={`ml-2 px-2 py-0.5 rounded font-body text-sm font-bold ${confidenceColor(conf)}`}>
          {confidenceLabel(conf)}
        </span>
        <span className="ml-auto font-body text-text-secondary text-sm">
          {new Date(item.created_at).toLocaleString('en-SG')}
        </span>
      </div>

      <div className="p-4 space-y-4">

        {/* Existing incident — read-only preview */}
        <div>
          <div className="font-body text-text-secondary mb-2 uppercase tracking-widest" style={{ fontSize: '10px' }}>
            Existing Incident
          </div>
          <div className="border border-border bg-bg p-3 space-y-2">
            <div className="font-body font-bold text-text-primary text-sm">{targetIncident.title}</div>
            <div className="font-body text-text-secondary text-sm leading-relaxed line-clamp-3">
              {targetIncident.summary}
            </div>
            <div className="flex gap-4 font-body text-text-secondary" style={{ fontSize: '11px' }}>
              <span>{targetIncident.incident_date}</span>
              <span>{targetIncident.source_urls.length} source{targetIncident.source_urls.length !== 1 ? 's' : ''}</span>
              {targetIncident.update_count > 0 && (
                <span className="text-cyan-400">{targetIncident.update_count} update{targetIncident.update_count !== 1 ? 's' : ''} already merged</span>
              )}
            </div>
          </div>
        </div>

        {/* New source */}
        <div>
          <div className="font-body text-text-secondary mb-2 uppercase tracking-widest" style={{ fontSize: '10px' }}>
            New Source
          </div>
          <div className="border border-border bg-bg p-3 space-y-1">
            <div className="font-body text-text-primary text-sm">{headline}</div>
            <a href={safeHref(newUrl)} target="_blank" rel="noopener noreferrer"
               className="font-body text-yellow text-sm hover:underline break-all">
              {newUrl}
            </a>
          </div>
        </div>

        {/* Updated summary — editable */}
        <div>
          <label className="font-body text-text-secondary text-sm uppercase tracking-widest block mb-1">
            Updated Summary{' '}
            <span className={summary.length > 0 && (summary.length < 500 || summary.length > 800) ? 'text-red' : 'text-green'}>
              {summary.length} chars
            </span>
          </label>
          <textarea
            value={summary}
            onChange={e => setSummary(e.target.value)}
            rows={5}
            placeholder="Edit the merged summary before confirming, or leave blank to keep existing…"
            className="w-full px-3 py-2 bg-bg border border-border text-text-primary font-body text-sm rounded focus:border-cyan-500 focus:outline-none resize-y"
          />
        </div>

        {/* Possible related banners */}
        {visibleRelated.length > 0 && (
          <div className="space-y-2">
            <div className="font-body text-text-secondary uppercase tracking-widest" style={{ fontSize: '10px' }}>
              Agent-suggested links
            </div>
            {visibleRelated.map(link => {
              const preview = relatedPreviews[link.incident_id]
              return (
                <div key={link.incident_id}
                     className="border border-yellow/30 bg-yellow/5 p-3 space-y-2">
                  <div className="flex items-start gap-2">
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
                  <div className="font-body text-text-secondary leading-snug" style={{ fontSize: '11px' }}>
                    {link.reason}
                  </div>
                  <div className="flex gap-2">
                    <button
                      onClick={() => handleConfirmLink(link)}
                      disabled={loading}
                      className="px-2 py-1 border border-yellow text-yellow font-body hover:bg-yellow hover:text-bg transition-colors disabled:opacity-50"
                      style={{ fontSize: '11px' }}
                    >
                      CONFIRM LINK
                    </button>
                    <button
                      onClick={() => handleDismissLink(link)}
                      disabled={loading}
                      className="px-2 py-1 border border-border text-text-secondary font-body hover:border-red hover:text-red transition-colors disabled:opacity-50"
                      style={{ fontSize: '11px' }}
                    >
                      DISMISS
                    </button>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* Action bar */}
      <div className="flex items-center gap-3 px-4 py-3 border-t border-cyan-500/40">
        {error && <span className="font-body text-red text-sm">{error}</span>}
        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={handleRejectUpdate}
            disabled={loading}
            className="px-3 py-2 border border-red text-red font-body text-sm hover:bg-red hover:text-bg transition-colors disabled:opacity-50"
          >
            Reject Update
          </button>
          <button
            onClick={handleSplit}
            disabled={loading}
            className="px-3 py-2 border border-border text-text-secondary font-body text-sm hover:border-text-secondary hover:text-text-primary transition-colors disabled:opacity-50"
          >
            Split into New
          </button>
          <button
            onClick={handleConfirmUpdate}
            disabled={loading}
            className="px-4 py-2 bg-cyan-600 text-white font-body text-sm font-bold hover:bg-cyan-500 transition-colors disabled:opacity-50"
          >
            {loading ? '…' : 'Confirm Update ✓'}
          </button>
        </div>
      </div>
    </article>
  )
}
