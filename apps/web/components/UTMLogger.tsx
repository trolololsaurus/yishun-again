'use client'

import { useEffect } from 'react'
import { useSearchParams } from 'next/navigation'

interface Props { incidentId?: string }

export function UTMLogger({ incidentId }: Props) {
  const searchParams = useSearchParams()

  useEffect(() => {
    const utm_source   = searchParams.get('utm_source')
    const utm_medium   = searchParams.get('utm_medium')
    const utm_campaign = searchParams.get('utm_campaign')

    console.log('UTMLogger firing with params:', { utm_source, utm_medium, utm_campaign })

    if (!utm_source && !utm_medium && !utm_campaign) return

    const payload = {
      utm_source:   utm_source   ?? 'unknown',
      utm_medium:   utm_medium   ?? 'link',
      utm_campaign: utm_campaign ?? 'unknown',
      incident_id:  incidentId  ?? null,
      referrer:     document.referrer || null,
    }

    fetch('/api/utm/log', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
      .then(async res => {
        console.log('UTM log response:', res.status, await res.text())
      })
      .catch(err => {
        console.error('UTM log fetch error:', err)
      })
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  return null
}
