import type { Metadata } from 'next'
import Link from 'next/link'
import Image from 'next/image'
import { supabase } from '@/lib/supabase'
import { SITE_URL } from '@/lib/site'
import { fmtDate } from '@/lib/utils'

export const revalidate = 300

export const metadata: Metadata = {
  title:       'Patterns',
  description: 'The recurring stories of Yishun — cats, the Devil’s Ring, and the estate’s own cast of characters, drawn from the incident archive.',
  alternates:  { canonical: `${SITE_URL}/patterns` },
  openGraph: {
    title:       'Patterns — Yishun Again',
    description: 'The recurring stories of Yishun, drawn from the incident archive.',
    url:         `${SITE_URL}/patterns`,
    images:      [{ url: `${SITE_URL}/og-default.jpg`, width: 1200, height: 630 }],
    type:        'website',
  },
  twitter: { card: 'summary_large_image' },
}

interface PatternRow {
  slug: string
  title: string
  thesis: string
  hero_image_url: string | null
  incident_ids: string[]
}

export default async function PatternsPage() {
  const { data: patterns } = await supabase
    .from('patterns')
    .select('slug,title,thesis,hero_image_url,incident_ids')
    .eq('published', true)
    .returns<PatternRow[]>()

  // Most incidents first. cardinality() isn't sortable in a PostgREST .order(),
  // so this sorts the small fetched set in JS rather than adding a SQL view.
  const rows = [...(patterns ?? [])]
    .sort((a, b) => (b.incident_ids?.length ?? 0) - (a.incident_ids?.length ?? 0))

  // One query for the date range across every pattern's incidents: gather all
  // ids, fetch (id, incident_date) once, then min/max per pattern in JS. Cheaper
  // than a query per row and trivial at this scale.
  const allIds = [...new Set(rows.flatMap(p => p.incident_ids ?? []))]
  const dateById = new Map<string, string>()
  if (allIds.length > 0) {
    const { data: incs } = await supabase
      .from('incidents')
      .select('id,incident_date')
      .in('id', allIds)
      .eq('is_published', true)
    for (const i of incs ?? []) if (i.incident_date) dateById.set(i.id, i.incident_date)
  }

  const rangeFor = (ids: string[]): string | null => {
    const dates = ids.map(id => dateById.get(id)).filter(Boolean).sort() as string[]
    if (dates.length === 0) return null
    const lo = fmtDate(dates[0]), hi = fmtDate(dates[dates.length - 1])
    return lo === hi ? lo : `${lo} — ${hi}`
  }

  return (
    <article className="max-w-2xl mx-auto px-4 py-10 w-full font-body">
      <h1 className="font-display text-amber mb-3 leading-relaxed" style={{ fontSize: '32px' }}>
        PATTERNS
      </h1>
      {/* Same body colour/size as /about's intro (text-text-primary, 16px) for consistency. */}
      <p className="text-text-primary leading-relaxed mb-8" style={{ fontSize: '16px' }}>
        Phenomena happen. And they happen in Yishun, stranger than fiction. These are the
        recurring stories the archive keeps surfacing — each one backed by the incidents underneath it.
      </p>

      {rows.length === 0 ? (
        <p className="text-text-secondary" style={{ fontSize: '16px' }}>No patterns published yet.</p>
      ) : (
        <ul className="space-y-4">
          {rows.map(p => {
            const range = rangeFor(p.incident_ids ?? [])
            const count = (p.incident_ids ?? []).length
            return (
              <li key={p.slug}>
                <Link
                  href={`/patterns/${p.slug}`}
                  className="flex gap-4 px-4 py-4 border border-border hover:bg-surface transition-colors group"
                >
                  {/* Hero thumbnail, left of the text. next/image reserves the
                      box and serves a small file — no art yet shows a neutral
                      placeholder rather than nothing, so the row height never
                      jumps once one is generated. */}
                  <div className="relative flex-none overflow-hidden border border-border bg-surface"
                       style={{ width: 96, height: 96 }}>
                    {p.hero_image_url ? (
                      // The art pipeline renders with NEAREST resampling to keep its pixel-art
                      // edges stepped (art/generate_image.py). next/image's own server-side
                      // resize smooths that away before the browser ever sees it — imageRendering
                      // can't un-blur a file that's already been resampled — so skip the
                      // optimizer here and let the browser do the (pixelated) downscale itself.
                      <Image src={p.hero_image_url} alt="" fill sizes="96px" className="object-cover"
                             unoptimized style={{ imageRendering: 'pixelated' }} />
                    ) : (
                      <span className="absolute inset-0 flex items-center justify-center font-body text-text-secondary"
                            style={{ fontSize: 9 }}>
                        NO ART
                      </span>
                    )}
                  </div>

                  <div className="min-w-0 flex-1">
                    <h2 className="font-body font-bold text-text-primary group-hover:text-amber-lt transition-colors mb-1"
                        style={{ fontSize: '18px' }}>
                      {p.title}
                    </h2>
                    <p className="text-text-secondary leading-relaxed mb-2 line-clamp-2" style={{ fontSize: '14px' }}>
                      {p.thesis.slice(0, 160).trimEnd()}{p.thesis.length > 160 ? '…' : ''}
                    </p>
                    <div className="text-text-secondary" style={{ fontSize: '12px' }}>
                      {count} incident{count !== 1 ? 's' : ''}{range ? ` · ${range}` : ''}
                    </div>
                  </div>
                </Link>
              </li>
            )
          })}
        </ul>
      )}
    </article>
  )
}
