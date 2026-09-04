'use client'

import { useState } from 'react'
import { getSessionId, sendTrackingBeacon } from '@/lib/tracking'

// Shared by incident and pattern pages. incidentId is omitted (stays null in
// page_events) on any page that isn't backed by a real incidents.id — the
// column is a real FK (migration 019), so passing a non-incident id would
// either fail the insert or, worse, silently misattribute the share.
export function ShareButton({ url, title, incidentId }: { url: string; title: string; incidentId?: string }) {
  const [copied, setCopied] = useState(false)

  function logShare() {
    sendTrackingBeacon({
      id:          crypto.randomUUID(),
      session_id:  getSessionId(),
      incident_id: incidentId ?? null,
      path:        window.location.pathname,
      event_type:  'share',
    })
  }

  async function handleShare() {
    logShare()
    try {
      await navigator.clipboard.writeText(url)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // Fallback for browsers without clipboard API
      const el = document.createElement('textarea')
      el.value = url
      document.body.appendChild(el)
      el.select()
      document.execCommand('copy')
      document.body.removeChild(el)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    }
  }

  return (
    <button
      onClick={handleShare}
      className="min-h-[48px] px-4 py-2 border border-border text-text-secondary font-body hover:border-amber-lt hover:text-amber-lt transition-colors"
      style={{ fontSize: '14px' }}
      aria-label="Copy link to clipboard"
    >
      {copied ? 'Copied!' : '⎘ Share'}
    </button>
  )
}
