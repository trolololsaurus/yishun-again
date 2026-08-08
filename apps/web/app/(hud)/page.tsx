import type { Metadata } from 'next'
import { Suspense }      from 'react'
import { supabase }      from '@/lib/supabase'
import { FeedBody }      from '@/components/FeedBody'
import { SITE_URL }      from '@/lib/site'

export const revalidate = 60  // 60-second ISR for production

const FEED_TITLE       = 'Yishun Again — Live Incident Feed & Chaos Index | Singapore'
const FEED_DESCRIPTION =
  'A satirical live feed of every strange, dark, and heartwarming incident in ' +
  "Yishun (Nee Soon), Singapore — the estate's ongoing chronicle, classified " +
  'and scored on the Chaos Index.'

export const metadata: Metadata = {
  // `absolute` opts out of the layout's '%s · Yishun Again' template — the feed
  // title already carries the brand.
  title:       { absolute: FEED_TITLE },
  description: FEED_DESCRIPTION,
  alternates:  { canonical: `${SITE_URL}/` },
  openGraph: {
    title:       FEED_TITLE,
    description: FEED_DESCRIPTION,
    url:         `${SITE_URL}/`,
    images:      [{ url: `${SITE_URL}/og-default.jpg`, width: 1200, height: 630 }],
    type:        'website',
  },
  twitter: { card: 'summary_large_image' },
}

// Site-level structured data lives on the root (Feed) route — WebSite +
// Organization nodes, same inline <script> pattern as incidents/[slug]/page.tsx.
const siteJsonLd = [
  {
    '@context':  'https://schema.org',
    '@type':     'WebSite',
    name:        'Yishun Again',
    url:         `${SITE_URL}/`,
    description:
      'Satirical live incident feed and archive for Yishun (Nee Soon), Singapore, scored on the Chaos Index.',
    inLanguage:  'en-SG',
  },
  {
    '@context': 'https://schema.org',
    '@type':    'Organization',
    name:       'Yishun Again',
    url:        `${SITE_URL}/`,
    logo:       `${SITE_URL}/og-default.jpg`,
  },
]

export default async function FeedPage() {
  const currentYear = new Date().getFullYear()

  // Feed page 0. Latest incident always on top: sort by event date (newest
  // first), id as a stable tiebreaker. MUST match /api/incidents so SSR page 0
  // and the load-more pages stay consistent. All-time (no year filter) — the
  // client re-fetches page 0 with the selected year; these SSR rows exist so
  // crawlers see linked incidents. Chip counts come from the Chaos panel now, so
  // this page no longer queries them.
  const { data: feedRows } = await supabase
    .from('incidents')
    .select('id,slug,title,classification,custom_label,severity,corroboration_count,published_at,incident_date,area_name,is_milestone,milestone_type,milestone_value,is_developing,update_count,first_reported_at,source_urls,source_timeline,latest_source_role,pixel_art_url')
    .eq('is_published', true)
    .order('incident_date', { ascending: false, nullsFirst: false })
    .order('id',            { ascending: false })
    .limit(20)

  return (
    <>
      {/* The page <h1> is the logo in <Nav>, rendered on the HUD roots. */}
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(siteJsonLd) }}
      />
      <Suspense fallback={null}>
        <FeedBody
          initialFeed={(feedRows ?? []) as any}
          currentYear={currentYear}
        />
      </Suspense>
    </>
  )
}
