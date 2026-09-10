import { NextResponse } from 'next/server'
import { supabase } from '@/lib/supabase'
import { SITE_URL } from '@/lib/site'
import { PUBLIC_INCIDENT_COLUMNS } from '@/lib/publicColumns'
import { escapeHtml } from '@/lib/utils'

// Only fields already in PUBLIC_INCIDENT_COLUMNS — asserted below so drift
// in the allowlist breaks the build instead of silently exposing nothing new.
const FEED_COLUMNS = 'id,slug,title,summary,published_at'
if (FEED_COLUMNS.split(',').some(c => !PUBLIC_INCIDENT_COLUMNS.includes(c))) {
  throw new Error('feed.xml: a FEED_COLUMNS field is missing from PUBLIC_INCIDENT_COLUMNS')
}

// Teaser only — never the full summary (that's the incident page's job).
function teaser(summary: string | null, max = 200): string {
  const text = (summary ?? '').trim()
  if (text.length <= max) return text
  const cut = text.slice(0, max)
  const lastSpace = cut.lastIndexOf(' ')
  return (lastSpace > 0 ? cut.slice(0, lastSpace) : cut) + '…'
}

export async function GET() {
  const { data } = await supabase
    .from('incidents')
    .select(FEED_COLUMNS)
    .eq('is_published', true)
    .order('published_at', { ascending: false, nullsFirst: false })
    .limit(50)

  const rows = (data ?? []).filter(inc => inc.published_at)

  const items = rows.map(inc => {
    const url = `${SITE_URL}/incidents/${inc.slug}`
    return `    <item>
      <title>${escapeHtml(inc.title)}</title>
      <link>${url}</link>
      <guid isPermaLink="true">${url}</guid>
      <pubDate>${new Date(inc.published_at!).toUTCString()}</pubDate>
      <description>${escapeHtml(teaser(inc.summary))}</description>
    </item>`
  }).join('\n')

  const lastBuildDate = rows[0]?.published_at
    ? new Date(rows[0].published_at).toUTCString()
    : new Date().toUTCString()

  const xml = `<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Yishun Again</title>
    <link>${SITE_URL}</link>
    <description>Singapore's Most Cursed Estate — Documented.</description>
    <language>en-sg</language>
    <lastBuildDate>${lastBuildDate}</lastBuildDate>
${items}
  </channel>
</rss>
`

  return new NextResponse(xml, {
    headers: { 'Content-Type': 'application/rss+xml; charset=utf-8' },
  })
}
