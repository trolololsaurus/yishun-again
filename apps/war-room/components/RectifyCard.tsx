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
    return <p className="text-text-secondary">Nothing left in this session.</p>
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
  const [notes,   setNotes]   = useState('')
  const [busy,    setBusy]    = useState<null | 'rectify' | 'no-image'>(null)
  const [error,   setError]   = useState<string | null>(null)
  const [warning, setWarning] = useState<string | null>(null)
  const [result,  setResult]  = useState<{ url: string | null; status: string; attempts: ImageAttempt[] } | null>(null)
  const [confirmNoImage, setConfirmNoImage] = useState(false)

  const shown = result?.attempts ?? attempts
  // A render landed and is on screen waiting to be judged. This is the state the
  // card previously could not be in: success called onResolved() immediately, so
  // the row vanished before the operator ever saw the picture they made.
  const reviewing = result?.status === 'ok' && !!result.url
  const imageUrl  = result?.url ?? item.pixel_art_url

  /**
   * Send the prompt for rendering.
   *
   * `notes` is appended rather than replacing anything. An operator who wants
   * "just 1 snake" types six words; before this existed those six words became
   * the ENTIRE prompt, discarding the ~4000 characters of scene and style that
   * produced the image they were trying to adjust. Amending is what they mean.
   */
  function composeSubmission(): string {
    const base = draft.trim()
    const add  = notes.trim()
    if (!add) return base
    if (!base) return add
    return `${base}\n\nOPERATOR ADJUSTMENTS (these take priority):\n${add}`
  }

  async function post(path: 'rectify' | 'no-image', body?: Record<string, unknown>) {
    setBusy(path)
    setError(null)
    setWarning(null)
    setConfirmNoImage(false)
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

      setResult({ url: data.url ?? null, status: data.status, attempts: data.attempts ?? [] })

      // Fill the box with the prompt that was ACTUALLY rendered from, so the
      // next edit is an edit. On the compose path the operator started from an
      // empty box and has no other way to see what the model wrote.
      if (typeof data.final_prompt === 'string' && data.final_prompt) {
        setDraft(data.final_prompt)
      }
      // The adjustments have been folded into the prompt now; leaving them in
      // the box would silently apply them twice on the next render.
      setNotes('')

      if (data.status !== 'ok') {
        setError(`Render ${data.status}. See the latest attempt below, adjust and try again.`)
        return
      }
      if (data.revalidated === false) {
        setWarning(
          'Image saved, but the live page was NOT revalidated' +
          (data.revalidate_reason ? ` (${data.revalidate_reason})` : '') +
          ' — it will keep serving the placeholder for up to an hour.',
        )
      }
      // Deliberately NOT resolved here. The operator judges the image and
      // presses "Keep image" or "Reject & regenerate".
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
          <div className="text-text-primary">
            <span className={classColor(item.classification, item.custom_label)}>
              {classIcon(item.classification, item.custom_label)}{' '}
              {classLabel(item.classification, item.custom_label)}
            </span>
            <span className="text-text-secondary"> · {severityDiamonds(item.severity)}</span>
          </div>
          <Link
            href={`/incidents/${item.slug}`}
            className="text-text-primary hover:text-yellow"
          >
            {item.title}
          </Link>
          <div className="text-text-secondary text-xs">
            {[item.area_name, item.block_number].filter(Boolean).join(' · ') || '—'}
          </div>
        </div>
        {/* Colour by meaning. This was always text-red, so a successful render
            announced itself in the failure colour. */}
        <span
          className={`uppercase tracking-widest text-xs ${
            (result?.status ?? item.image_status) === 'ok' ? 'text-green' : 'text-red'
          }`}
        >
          {result?.status ?? item.image_status ?? 'unknown'}
        </span>
      </div>

      <div className="px-4 py-3 space-y-3">
        {imageUrl && (
          <div className="space-y-1">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={imageUrl}
              alt={`Art for ${item.title}`}
              className="w-full border border-border"
            />
            {reviewing && (
              <p className="text-text-secondary text-xs">
                Rendered just now. Keep it, or adjust the prompt below and regenerate.
              </p>
            )}
          </div>
        )}

        {/* Every attempt, including a single one. ArtPromptModal gates this on
            length > 1, which hides the lone refusal reason — the one thing the
            operator actually needs in order to edit the prompt usefully. */}
        {shown.length > 0 && (
          <div className="space-y-2">
            <div className="text-text-secondary uppercase tracking-widest text-xs">
              Attempts ({shown.length})
            </div>
            {shown.map(a => (
              <details key={a.n} className="border border-border rounded">
                <summary className="px-2 py-1 cursor-pointer text-xs">
                  <span className="text-text-primary">#{a.n} {a.outcome.toUpperCase()}</span>
                  {a.reason && <span className="text-text-secondary"> — {a.reason}</span>}
                </summary>
                <pre className="px-2 py-2 bg-bg border-t border-border whitespace-pre-wrap text-text-secondary text-xs">{a.prompt}</pre>
              </details>
            ))}
          </div>
        )}

        {/* WHAT TO CHANGE — the common case, so it is the visible one.
            Nobody wants to hand-edit 4000 characters of scene description to
            say "one snake, not two". This box is short, always starts empty,
            and is APPENDED to the prompt rather than replacing it. */}
        <div>
          <label className="text-text-secondary uppercase tracking-widest block mb-1 text-xs">
            What to change
          </label>
          <textarea
            value={notes}
            onChange={e => setNotes(e.target.value)}
            rows={3}
            maxLength={2000}
            placeholder={'e.g. Only one snake.\nPut the python in the foreground.\nFewer people.'}
            className="w-full px-3 py-2 bg-bg border border-border text-text-primary text-sm rounded focus:border-yellow focus:outline-none resize-y placeholder:text-text-secondary/50"
          />
          <div className="text-text-secondary text-xs">
            {draft.trim()
              ? 'Added to the prompt below — the rest of it is kept.'
              : 'No prompt stored yet. Leave this empty and press Generate to have one written for you.'}
          </div>
        </div>

        {/* The full prompt, collapsed. Available for real rewrites, out of the
            way for the 90% case above. */}
        <details className="border border-border rounded">
          <summary className="px-2 py-1 text-text-secondary cursor-pointer text-xs">
            Full prompt ({draft.length} / {MAX_PROMPT_CHARS} chars) — edit directly
          </summary>
          <div className="px-2 py-2 border-t border-border">
            <textarea
              value={draft}
              onChange={e => setDraft(e.target.value)}
              rows={12}
              maxLength={MAX_PROMPT_CHARS}
              className="w-full px-3 py-2 bg-bg border border-border text-text-primary text-sm rounded focus:border-yellow focus:outline-none resize-y"
            />
            <div className="text-text-secondary text-xs">
              {draft.trim()
                ? 'Rendered as one attempt, with no automatic softening.'
                : 'Empty — a prompt will be composed for you, then run through the full softening ladder.'}
            </div>
          </div>
        </details>

        {error   && <p className="text-red text-sm">{error}</p>}
        {warning && <p className="text-yellow text-sm">{warning}</p>}
      </div>

      {/* Two modes. Before a render the question is "make me an image"; after
          one it is "is this image good enough". Showing both sets at once is
          what made this confusing — and "Retry with edits" vs "Retry as-is"
          named an implementation detail (which prompt source is used) rather
          than anything the operator wants.
          There is still deliberately NO control that can set or clear
          `suppressed` — guardrail #5 is not operator-overridable. */}
      <div className="px-4 py-3 border-t border-border flex flex-wrap gap-2 items-center">
        {reviewing ? (
          <>
            <button
              onClick={() => onResolved(item.id)}
              disabled={busy !== null}
              className="px-4 py-2 bg-green text-bg text-sm font-bold rounded disabled:opacity-40"
            >
              Keep image
            </button>

            <button
              onClick={() => post('rectify', { prompt: composeSubmission() })}
              disabled={busy !== null}
              className="px-4 py-2 border border-yellow text-yellow text-sm rounded disabled:opacity-40"
              title="Discard this render and generate another from the prompt below"
            >
              {busy === 'rectify' ? 'Regenerating…' : 'Reject & regenerate'}
            </button>
          </>
        ) : (
          <button
            onClick={() => post('rectify', draft.trim() || notes.trim()
              ? { prompt: composeSubmission() }
              : undefined)}
            disabled={busy !== null}
            className="px-4 py-2 bg-green text-bg text-sm font-bold rounded disabled:opacity-40"
          >
            {busy === 'rectify'
              ? 'Rendering…'
              : draft.trim() ? 'Regenerate' : 'Generate image'}
          </button>
        )}

        <button
          onClick={() => (confirmNoImage ? post('no-image') : setConfirmNoImage(true))}
          disabled={busy !== null}
          className="px-4 py-2 border border-red text-red text-sm rounded disabled:opacity-40"
        >
          {/* The incident is already published — that is what the page header
              says. Naming this "Publish without image" implied the opposite. */}
          {confirmNoImage ? 'Confirm — give up on an image' : 'Give up on an image'}
        </button>

        <button
          onClick={() => onDismiss(item.id)}
          disabled={busy !== null}
          className="px-4 py-2 text-sm text-text-secondary hover:text-text-primary disabled:opacity-40"
        >
          Decide later
        </button>

        {confirmNoImage && (
          <span className="text-text-secondary text-xs">
            Terminal — future backfills will skip this incident.
          </span>
        )}
      </div>
    </article>
  )
}
