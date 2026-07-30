'use client'

import { useEffect } from 'react'
import { usePathname, useSearchParams } from 'next/navigation'

interface Props { incidentId?: string }

export function UTMLogger({ incidentId }: Props) {
  const searchParams = useSearchParams()
  const pathname     = usePathname()

  useEffect(() => {
    // Mounted twice on incident pages: once from the root layout (no
    // incidentId) and once from the page (with incidentId). Only the page
    // instance logs there, or every share click writes two utm_events rows.
    if (!incidentId && pathname?.startsWith('/incidents/')) return

    const utm_source   = searchParams.get('utm_source')
    const utm_medium   = searchParams.get('utm_medium')
    const utm_campaign = searchParams.get('utm_campaign')

    if (!utm_source && !utm_medium && !utm_campaign) return

    const payload = {
      utm_source:   utm_source   ?? 'unknown',
      utm_medium:   utm_medium   ?? 'link',
      utm_campaign: utm_campaign ?? 'unknown',
      incident_id:  incidentId  ?? null,
      referrer:     document.referrer || null,
    }

    // QA L2: fire-and-forget; no debug logging of the response body in prod.
    fetch('/api/utm/log', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }).catch(() => { /* analytics best-effort */ })
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  return null
}
