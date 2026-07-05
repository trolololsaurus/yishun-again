import type { Metadata } from 'next'
import { TimelineClient } from './TimelineClient'
import { SITE_URL } from '@/lib/site'

export const metadata: Metadata = {
  title:       'Timeline',
  description: 'Full archive of every documented Yishun incident, sorted by date.',
  alternates:  { canonical: `${SITE_URL}/timeline` },
  openGraph: {
    title:       'Yishun Again — Timeline',
    description: 'Full archive of every documented Yishun incident, sorted by date.',
    url:         `${SITE_URL}/timeline`,
    images:      [{ url: `${SITE_URL}/og-default.jpg`, width: 1200, height: 630 }],
    type:        'website',
  },
  twitter: { card: 'summary_large_image' },
}

export default function TimelinePage() {
  return (
    <div className="max-w-3xl mx-auto px-4 py-6 w-full flex-1 flex flex-col">
      <h1 className="font-display text-amber mb-4" style={{ fontSize: '32px' }}>TIMELINE</h1>
      <p className="font-body text-text-secondary mb-6" style={{ fontSize: '16px' }}>
        Every documented incident. Filter by classification, severity, or date.
      </p>
      <TimelineClient />
    </div>
  )
}
