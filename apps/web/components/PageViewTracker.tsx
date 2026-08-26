'use client'

import { useEffect, useRef } from 'react'
import { usePathname } from 'next/navigation'
import { getSessionId, sendTrackingBeacon } from '@/lib/tracking'

interface Props { incidentId?: string }

// Fires on every pageview (not just UTM-tagged arrivals — see UTMLogger),
// plus a best-effort dwell-time beacon when the tab hides or this page is
// left. Powers bounce rate / dwell time, which neither Cloudflare's zone
// analytics nor utm_events can answer — see migration 019_page_events.sql.
export function PageViewTracker({ incidentId }: Props) {
  const pathname       = usePathname()
  const eventIdRef      = useRef<string | null>(null)
  const mountedAtRef    = useRef(0)
  // Persists across client-side navigations because this component is
  // mounted once in the root layout and never unmounts for an in-app route
  // change — lets a same-site "referrer" reflect actual in-app browsing
  // (e.g. feed -> incident) instead of staying frozen at document.referrer,
  // which the browser never updates after the very first load.
  const prevPathRef     = useRef<string | null>(null)

  useEffect(() => {
    // Mounted twice on incident pages: once from the root layout (no
    // incidentId) and once from the page (with incidentId). Only the page
    // instance logs there, same reason as UTMLogger.
    if (!incidentId && pathname?.startsWith('/incidents/')) return

    const id = crypto.randomUUID()
    eventIdRef.current   = id
    mountedAtRef.current = Date.now()

    const referrer = prevPathRef.current
      ? `${window.location.origin}${prevPathRef.current}`
      : (document.referrer || null)

    const createPayload = {
      id,
      session_id:  getSessionId(),
      incident_id: incidentId ?? null,
      path:        pathname,
      referrer,
    }

    // sendBeacon, not fetch: a fast bounce can fire the dwell beacon (below)
    // before a fetch() for this create request has finished, and an UPDATE
    // racing ahead of its own INSERT silently matches zero rows — losing
    // dwell data on exactly the visits most worth measuring. sendBeacon uses
    // the same delivery path for both, so the browser can't reorder them.
    if (!sendTrackingBeacon(createPayload)) {
      fetch('/api/track/pageview', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify(createPayload),
      }).catch(() => { /* analytics best-effort */ })
    }

    prevPathRef.current = pathname

    function sendDwell() {
      if (!eventIdRef.current) return
      const dwell_ms = Date.now() - mountedAtRef.current
      sendTrackingBeacon({ id: eventIdRef.current, dwell_ms })
      eventIdRef.current = null // guard against a duplicate send from the second listener
    }

    function onVisibilityChange() {
      if (document.visibilityState === 'hidden') sendDwell()
    }

    document.addEventListener('visibilitychange', onVisibilityChange)
    window.addEventListener('pagehide', sendDwell)

    return () => {
      sendDwell() // covers client-side (SPA) navigation away from this page
      document.removeEventListener('visibilitychange', onVisibilityChange)
      window.removeEventListener('pagehide', sendDwell)
    }
  }, [pathname, incidentId])

  return null
}
