'use client'

import { useState } from 'react'
import Link from 'next/link'
import { classColor, classIcon, classLabel, severityDiamonds } from '@/lib/utils'
// RECTIFY_COLUMNS and RectifyItem live in lib/types.ts, not here. A server
// component cannot import a runtime VALUE from a 'use client' module — it gets
// a client-reference proxy, which typechecks and then fails at request time.
import type { ImageAttempt, RectifyItem } from '@/lib/types'

export type { RectifyItem }

const MAX_PROMPT_CHARS = 8000

export function RectifyList({ initialItems }: { initialItems: RectifyItem[] }) {
  const [items, setItems] = useState(initialItems)

  // "Leave pending" is action 4 and is purely local: no request, no write, the
  // row simply drops out of this session's list and is still there next load.
  function dismiss(id: string) {
    setItems(prev => prev.filter(i => i.id !== id))
  }

  function resolve(id: string) {
    setItems(prev => prev.filter(i => i.id !== id))
  }

  if (items.length === 0) {
    return <p className="font-body text-text-secondary">Nothing left in this session.</p>
  }

  return (
    <div className="space-y-4">
      {items.map(item => (
        <RectifyCard key={item.id} item={item} onDismiss={dismiss} onResolved={resolve} />
      ))}
    </div>
  )
}

function RectifyCard({
  item, onDismiss, onResolved,
}: {
  item:       RectifyItem
  onDismiss:  (id: string) => void
  onResolved: (id: string) => void
}) {
  const attempts = item.image_attempts ?? []
  const [draft,   setDraft]   = useState(item.image_prompt ?? attempts.at(-1)?.prompt ?? '')
  const [busy,    setBusy]    = useState<null | 'rectify' | 'no-image'>(null)
  const [error,   setError]   = useState<string | null>(null)
  const [warning, setWarning] = useState<string | null>(null)
  const [result,  setResult]  = useState<{ url: string | null; status: string; attempts: ImageAttempt[] } | null>(null)
  const [confirmNoImage, setConfirmNoImage] = useState(false)

  const shown = result?.attempts ?? attempts

  async function post(path: 'rectify' | 'no-image', body?: Record<string, unknown>) {
    setBusy(path)
    setError(null)
    setWarning(null)
    try {
      const res = await fetch(`/api/incidents/${item.id}/${path}`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    body ? JSON.stringify(body) : undefined,
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(data.error ?? `HTTP ${res.status}`)

      if (path === 'no-image') {
        onResolved(item.id)
        return
      }

      // A rectify that did not render is NOT a success — the row stays in the
      // list with its new refusal reason so the operator can edit and try again.
      setResult({ url: data.url ?? null, status: data.status, attempts: data.attempts ?? [] })
      if (data.status !== 'ok') {
        setError(`Render ${data.status}. See the latest attempt below, edit the prompt and retry.`)
        return
      }
      if (data.revalidated === false) {
        setWarning(
          'Image saved, but the live page was NOT revalidated' +
          (data.revalidate_reason ? ` (${data.revalidate_reason})` : '') +
          ' — it will keep serving the placeholder for up to an hour.',
        )
        return
      }
      onResolved(item.id)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unknown error')
    } finally {
      setBusy(null)
    }
  }

  return (
    <article className="bg-surface border border-border rounded">
      <div className="px-4 py-3 border-b border-border flex items-start justify-between gap-4">
        <div>
          <div className="font-body text-text-primary">
            <span className={classColor(item.classification, item.custom_label)}>
              {classIcon(item.classification, item.custom_label)}{' '}
              {classLabel(item.classification, item.custom_label)}
            </span>
            <span className="text-text-secondary"> · {severityDiamonds(item.severity)}</span>
          </div>
          <Link
            href={`/incidents/${item.slug}`}
            className="font-body text-text-primary hover:text-yellow"
          >
            {item.title}
          </Link>
          <div className="font-body text-text-secondary" style={{ fontSize: '11px' }}>
            {[item.area_name, item.block_number].filter(Boolean).join(' · ') || '—'}
          </div>
        </div>
        <span className="font-body text-red uppercase tracking-widest" style={{ fontSize: '11px' }}>
          {result?.status ?? item.image_status ?? 'unknown'}
        </span>
      </div>

      <div className="px-4 py-3 space-y-3">
        {result?.url && (
          <img
            src={result.url}
            alt={`Rectified art for ${item.title}`}
            className="w-full max-w-xl border border-border"
          />
        )}

        {/* Every attempt, including a single one. ArtPromptModal gates this on
            length > 1, which hides the lone refusal reason — the one thing the
            operator actually needs in order to edit the prompt usefully. */}
        {shown.length > 0 && (
          <div className="space-y-2">
            <div className="font-body text-text-secondary uppercase tracking-widest"
                 style={{ fontSize: '11px' }}>
              Attempts ({shown.length})
            </div>
            {shown.map(a => (
              <details key={a.n} className="border border-border rounded">
                <summary className="px-2 py-1 font-body cursor-pointer" style={{ fontSize: '11px' }}>
                  <span className="text-text-primary">#{a.n} {a.outcome.toUpperCase()}</span>
                  {a.reason && <span className="text-text-secondary"> — {a.reason}</span>}
                </summary>
                <pre className="px-2 py-2 bg-bg border-t border-border font-body whitespace-pre-wrap text-text-secondary"
                     style={{ fontSize: '11px' }}>{a.prompt}</pre>
              </details>
            ))}
          </div>
        )}

        <div>
          <label className="font-body text-text-secondary uppercase tracking-widest block mb-1"
                 style={{ fontSize: '11px' }}>
            Prompt
          </label>
          <textarea
            value={draft}
            onChange={e => setDraft(e.target.value)}
            rows={10}
            maxLength={MAX_PROMPT_CHARS}
            className="w-full px-3 py-2 bg-bg border border-border text-text-primary font-body text-sm rounded focus:border-yellow focus:outline-none resize-y"
          />
          {/* The two paths behave differently and the operator is choosing
              between them, so the copy has to say which one they get. An empty
              box means no prompt was ever composed (generation failed at or
              before the HTTP boundary), and "Retry as-is" then runs the FULL
              generate path — scene writer plus softening ladder — not the
              single bare attempt this line used to promise unconditionally. */}
          <div className="font-body text-text-secondary" style={{ fontSize: '11px' }}>
            {draft.trim()
              ? `${draft.length} / ${MAX_PROMPT_CHARS} · one attempt, no automatic softening`
              : `${draft.length} / ${MAX_PROMPT_CHARS} · no prompt stored — “Retry as-is” will compose one and run the full softening ladder`}
          </div>
        </div>

        {error   && <p className="font-body text-red text-sm">{error}</p>}
        {warning && <p className="font-body text-yellow text-sm">{warning}</p>}
      </div>

      {/* Exactly four actions. There is deliberately NO control here that can
          set or clear `suppressed` — guardrail #5 is not operator-overridable. */}
      <div className="px-4 py-3 border-t border-border flex flex-wrap gap-2 items-center">
        <button
          onClick={() => post('rectify', { prompt: draft })}
          disabled={busy !== null || !draft.trim()}
          className="px-4 py-2 bg-green text-bg font-body text-sm font-bold rounded disabled:opacity-40"
        >
          {busy === 'rectify' ? 'Rendering…' : 'Retry with edits'}
        </button>

        <button
          onClick={() => post('rectify')}
          disabled={busy !== null}
          className="px-4 py-2 border border-yellow text-yellow font-body text-sm rounded disabled:opacity-40"
        >
          Retry as-is
        </button>

        <button
          onClick={() => (confirmNoImage ? post('no-image') : setConfirmNoImage(true))}
          disabled={busy !== null}
          className="px-4 py-2 border border-red text-red font-body text-sm rounded disabled:opacity-40"
        >
          {confirmNoImage ? 'Confirm — publish without image' : 'Publish without image'}
        </button>

        <button
          onClick={() => onDismiss(item.id)}
          disabled={busy !== null}
          className="px-4 py-2 font-body text-sm text-text-secondary hover:text-text-primary disabled:opacity-40"
        >
          Leave pending
        </button>

        {confirmNoImage && (
          <span className="font-body text-text-secondary" style={{ fontSize: '11px' }}>
            Terminal — future backfills will skip this incident.
          </span>
        )}
      </div>
    </article>
  )
}
