import type { Metadata } from 'next'
import { notFound } from 'next/navigation'
import Link from 'next/link'
import Image from 'next/image'
import { supabase } from '@/lib/supabase'
import { SITE_URL } from '@/lib/site'
import { PUBLIC_INCIDENT_COLUMNS } from '@/lib/publicColumns'
import { toParagraphs } from '@/lib/utils'
import { PatternTimeline } from '@/components/PatternTimeline'
import type { Incident } from '@/lib/types'

export const revalidate = 300

interface Props { params: Promise<{ slug: string }> }

interface Pattern {
  slug: string
  title: string
  thesis: string
  hero_image_url: string | null
  incident_ids: string[]
  updated_at: string
}

async function getPattern(slug: string): Promise<Pattern | null> {
  const clean = slug.replace(/[^a-z0-9-]/g, '')
  if (!clean) return null
  const { data } = await supabase
    .from('patterns')
    .select('slug,title,thesis,hero_image_url,incident_ids,updated_at')
    .eq('slug', clean)
    .eq('published', true)
    .returns<Pattern>()
    .single()
  return data ?? null
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const pattern = await getPattern((await params).slug)
  if (!pattern) return { title: 'Pattern not found' }

  const url  = `${SITE_URL}/patterns/${pattern.slug}`
  const desc = pattern.thesis.replace(/\s+/g, ' ').trim().slice(0, 160)
  const image = pattern.hero_image_url
    ? [{ url: pattern.hero_image_url, width: 1200, height: 630 }]
    : [{ url: `${SITE_URL}/og-default.jpg`, width: 1200, height: 630 }]

  return {
    title:       pattern.title,
    description: desc,
    alternates:  { canonical: url },
    openGraph:   { title: pattern.title, description: desc, url, images: image, type: 'article' },
    twitter:     { card: 'summary_large_image' },
  }
}

export default async function PatternPage({ params }: Props) {
  const pattern = await getPattern((await params).slug)
  if (!pattern) notFound()

  const ids = pattern.incident_ids ?? []
  let incidents: Incident[] = []
  if (ids.length > 0) {
    const { data } = await supabase
      .from('incidents')
      .select(PUBLIC_INCIDENT_COLUMNS)
      .in('id', ids)
      .eq('is_published', true)
      .order('incident_date', { ascending: true, nullsFirst: false })
      .returns<Incident[]>()
    incidents = data ?? []
  }

  const url = `${SITE_URL}/patterns/${pattern.slug}`
  // Same enrichment fields as the incident page's NewsArticle JSON-LD
  // (commit 38903d6) — dateModified, isAccessibleForFree, inLanguage and an
  // explicit publisher, so a citable page here is held to the same GEO bar
  // as an individual incident rather than a thinner schema.
  const jsonLd = {
    '@context':    'https://schema.org',
    '@type':       'CollectionPage',
    name:          pattern.title,
    description:   pattern.thesis.replace(/\s+/g, ' ').trim().slice(0, 160),
    url,
    dateModified:  pattern.updated_at,
    image:         pattern.hero_image_url ?? `${SITE_URL}/og-default.jpg`,
    isAccessibleForFree: true,
    inLanguage:    'en-SG',
    isPartOf:      { '@type': 'WebSite', name: 'Yishun Again', url: SITE_URL },
    publisher: {
      '@type': 'Organization',
      name:    'Yishun Again',
      url:     SITE_URL,
    },
    about: {
      '@type': 'DefinedTerm',
      name: 'Chaos Index',
      description: 'Yishun Again classification-weighted incident severity score',
      inDefinedTermSet: `${SITE_URL}/about`,
    },
    hasPart: incidents.map(i => ({
      '@type':       'NewsArticle',
      headline:      i.title,
      datePublished: i.incident_date,
      url:           `${SITE_URL}/incidents/${i.slug}`,
    })),
  }

  return (
    <article className="max-w-2xl mx-auto px-4 py-10 w-full font-body">
      {/* JSON.stringify leaves '<' unescaped — escape it so a title can't break
          out of this script block. Same guard as the incident page. */}
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd).replace(/</g, '\\u003c') }}
      />

      <div className="text-text-secondary uppercase mb-3" style={{ fontSize: '12px', letterSpacing: '0.08em' }}>
        Pattern · {incidents.length} incident{incidents.length !== 1 ? 's' : ''}
      </div>

      <h1 className="font-display text-amber mb-6 leading-relaxed" style={{ fontSize: '28px' }}>
        {pattern.title}
      </h1>

      {pattern.hero_image_url && (
        <div className="relative w-full aspect-video mb-6 border border-border overflow-hidden">
          <Image
            src={pattern.hero_image_url}
            alt={`Cover art for: ${pattern.title}`}
            fill
            sizes="(max-width: 640px) 100vw, 672px"
            className="object-cover"
            style={{ imageRendering: 'pixelated' }}
            priority
          />
        </div>
      )}

      {/* Full thesis — the server-rendered prose. Never truncated here. */}
      <div className="mb-8">
        {toParagraphs(pattern.thesis).map((para, i) => (
          <p key={i} className="text-text-primary leading-relaxed"
             style={{ fontSize: '16px', marginTop: i === 0 ? 0 : '1em' }}>
            {para}
          </p>
        ))}
      </div>

      <div className="font-body text-text-secondary mb-3 uppercase" style={{ fontSize: '14px' }}>
        The incidents
      </div>
      <div className="mb-8">
        <PatternTimeline incidents={incidents} />
      </div>

      <div className="border-t border-border pt-4">
        <Link href="/patterns" className="font-body text-text-secondary hover:text-text-primary"
              style={{ fontSize: '14px' }}>
          ← All patterns
        </Link>
      </div>
    </article>
  )
}
