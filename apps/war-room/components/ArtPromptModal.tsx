'use client'

import { useCallback, useEffect, useState } from 'react'

interface ArtPromptData {
  incident: {
    id:             string
    title:          string
    slug:           string
    classification: string
    custom_label:   string | null
    area_name:      string | null
    block_number:   string | null
    pixel_art_url:  string | null
    image_status:   string | null
  }
  prompt:   string | null
  status:   string
  attempts: Array<{ n: number; prompt: string; outcome: string; reason?: string }>
}

interface Props {
  incidentId: string
  title:      string
  onClose:    () => void
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState<'idle' | 'ok' | 'fail'>('idle')

  useEffect(() => {
    if (copied === 'idle') return
    const t = setTimeout(() => setCopied('idle'), 1500)
    return () => clearTimeout(t)
  }, [copied])

  async function copy() {
    // navigator.clipboard is undefined on insecure origins (plain-http local
    // testing) — report that rather than throwing an unhandled rejection.
    try {
      await navigator.clipboard.writeText(text)
      setCopied('ok')
    } catch {
      setCopied('fail')
    }
  }

  return (
    <button
      onClick={copy}
      className="px-2 py-0.5 border border-border text-text-secondary font-body hover:border-yellow hover:text-yellow transition-colors"
      style={{ fontSize: '11px' }}
    >
      {copied === 'ok' ? 'COPIED' : copied === 'fail' ? 'COPY FAILED' : 'COPY'}
    </button>
  )
}

function PromptBlock({ label, text, note }: { label: string; text: string; note?: string }) {
  return (
    <div>
      <div className="flex items-center gap-2 mb-1">
        <span className="font-body text-text-secondary uppercase tracking-widest text-sm">{label}</span>
        <CopyButton text={text} />
      </div>
      {note && (
        <div className="font-body text-text-secondary mb-1" style={{ fontSize: '10px' }}>{note}</div>
      )}
      <pre className="px-3 py-2 bg-bg border border-border text-text-primary font-body text-sm whitespace-pre-wrap break-words">
        {text}
      </pre>
    </div>
  )
}

export function ArtPromptModal({ incidentId, title, onClose }: Props) {
  const [data,  setData]  = useState<ArtPromptData | null>(null)
  const [error, setError] = useState<string | null>(null)

  // Close on Escape — the modal has no focusable element until it loads, so a
  // keydown listener on the document is what makes it dismissable throughout.
  const onKey = useCallback((e: KeyboardEvent) => {
    if (e.key === 'Escape') onClose()
  }, [onClose])

  useEffect(() => {
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onKey])

  useEffect(() => {
    let cancelled = false
    fetch(`/api/incidents/${incidentId}/art-prompt`)
      .then(async r => {
        const body = await r.json().catch(() => ({}))
        if (!r.ok) throw new Error(body.error ?? `HTTP ${r.status}`)
        return body as ArtPromptData
      })
      .then(d => { if (!cancelled) setData(d) })
      .catch(e => { if (!cancelled) setError(e instanceof Error ? e.message : 'Unknown error') })
    return () => { cancelled = true }
  }, [incidentId])

  return (
    <div
      className="fixed inset-0 z-50 bg-black/70 flex items-start justify-center overflow-auto p-6"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Art prompt"
        onClick={e => e.stopPropagation()}
        className="bg-surface border border-border rounded max-w-2xl w-full my-8"
      >
        {/* Header */}
        <div className="flex items-start gap-3 px-4 py-3 border-b border-border">
          <div className="min-w-0">
            <div className="font-body text-yellow font-bold uppercase tracking-widest text-sm">
              Art Prompt
            </div>
            <div className="font-body text-text-primary text-sm truncate">{title}</div>
          </div>
          <button
            onClick={onClose}
            className="ml-auto px-2 py-1 border border-border text-text-secondary font-body text-sm hover:border-red hover:text-red transition-colors"
          >
            ✕
          </button>
        </div>

        {/* Body */}
        <div className="p-4 space-y-4">
          {error && <div className="font-body text-red text-sm">{error}</div>}
          {!data && !error && <div className="font-body text-text-secondary text-sm">Loading…</div>}

          {data && (
            <>
              <div className="flex gap-4 flex-wrap font-body text-text-secondary text-sm">
                <span>Class: <span className="text-text-primary">{data.incident.custom_label ?? data.incident.classification}</span></span>
                <span>Area: <span className="text-text-primary">{data.incident.area_name ?? '— (defaults to “Yishun”)'}</span></span>
                {data.incident.block_number && <span>Blk {data.incident.block_number}</span>}
              </div>

              <div className="font-body text-text-secondary text-sm">
                Image status: <span className="text-text-primary uppercase">{data.status}</span>
                {data.status === 'suppressed' && ' — guardrail #5 (suicide / self-harm). Not retryable.'}
              </div>

              {data.prompt ? (
                <PromptBlock
                  label="Prompt used"
                  text={data.prompt}
                  note="The full assembled prompt of the last attempt — style, composition and physical-coherence blocks, the Haiku-written scene, then palette and exclusions."
                />
              ) : (
                <div className="font-body text-text-secondary text-sm">
                  No prompt recorded. Either this incident predates image generation, or it
                  was suppressed before any prompt was written.
                </div>
              )}

              {data.attempts.length > 1 && (
                <div className="space-y-2">
                  <div className="font-body text-text-secondary text-sm uppercase tracking-widest">
                    Attempts ({data.attempts.length})
                  </div>
                  {data.attempts.map(a => (
                    <div key={a.n} className="font-body text-sm">
                      <span className="text-text-primary">#{a.n} {a.outcome.toUpperCase()}</span>
                      {a.reason && <span className="text-text-secondary"> — {a.reason}</span>}
                    </div>
                  ))}
                </div>
              )}

              {!data.incident.pixel_art_url && (
                <div className="font-body text-text-secondary border-t border-border pt-3" style={{ fontSize: '11px' }}>
                  This incident has no pixel art. Generation is dormant — the prompt above is
                  what would run if it were re-enabled.
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}
