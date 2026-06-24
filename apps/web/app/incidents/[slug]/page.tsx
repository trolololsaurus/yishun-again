import type { Metadata } from 'next'
import { Suspense }      from 'react'
import { notFound }      from 'next/navigation'
import Link              from 'next/link'
import { supabase }      from '@/lib/supabase'
import { classIcon, classColor, classTooltip, HYPE_TOOLTIP, severityDiamonds, severityTooltip, hypeMeter, hypeFromSources, fmtDate, formatDuration, formatDurationGap, lastVerdictEntry, verdictNoun, collapseTimelineByDate } from '@/lib/utils'
import { ShareButton }   from './ShareButton'
import { UTMLogger }     from '@/components/UTMLogger'
import type { Incident, IncidentLink, RelatedIncident, SourceTimelineEntry } from '@/lib/types'

export const revalidate = 3600

interface Props { params: { slug: string } }

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const slug = params.slug.replace(/[^a-z0-9-]/g, '')
  const { data } = await supabase
    .from('incidents')
    .select('title,seo_title,seo_description,summary,pixel_art_url,slug')
    .eq('slug', slug).eq('is_published', true).single()

  if (!data) return { title: 'Incident not found' }

  const siteUrl   = process.env.NEXT_PUBLIC_SITE_URL ?? 'https://yishunagain.com'
  const ogTitle   = data.seo_title ?? data.title ?? slug
  const ogDesc    = (data.seo_description ?? (data.summary ?? '').slice(0, 160))
  const ogImage   = data.pixel_art_url
    ? [{ url: data.pixel_art_url, width: 1200, height: 630 }]
    : [{ url: `${siteUrl}/og-default.jpg`, width: 1200, height: 630 }]

  return {
    title:       ogTitle,
    description: ogDesc,
    openGraph: {
      title:       ogTitle,
      description: ogDesc,
      url:         `${siteUrl}/incidents/${slug}`,
      images:      ogImage,
    },
    twitter: { card: 'summary_large_image' },
  }
}

export default async function IncidentPage({ params }: Props) {
  const slug = params.slug.replace(/[^a-z0-9-]/g, '')
  if (!slug) notFound()

  const { data, error } = await supabase
    .from('incidents')
    .select('*')
    .eq('slug', slug)
    .eq('is_published', true)
    .single()

  if (error || !data) notFound()

  const incident = data as Incident
  const siteUrl  = process.env.NEXT_PUBLIC_SITE_URL ?? 'https://yishunagain.com'

  // Fetch confirmed related incident links. RLS already filters to
  // confirmed_by_operator=TRUE; QA L11 asserts it explicitly as defence-in-depth
  // so unconfirmed links can't leak if the policy is ever misconfigured.
  const [linksResult] = await Promise.all([
    supabase
      .from('incident_links')
      .select('incident_a,incident_b,link_type')
      .eq('confirmed_by_operator', true)
      .or(`incident_a.eq.${incident.id},incident_b.eq.${incident.id}`),
  ])

  let relatedIncidents: RelatedIncident[] = []
  const linkRows = (linksResult.data ?? []) as IncidentLink[]

  if (linkRows.length > 0) {
    const relatedIds = linkRows.map(l =>
      l.incident_a === incident.id ? l.incident_b : l.incident_a
    )
    const { data: relatedData } = await supabase
      .from('incidents')
      .select('id,slug,title,classification,custom_label,incident_date')
      .in('id', relatedIds)
      .eq('is_published', true)

    relatedIncidents = (relatedData ?? []).map(r => ({
      id:             r.id,
      slug:           r.slug,
      title:          r.title,
      classification: r.classification,
      custom_label:   r.custom_label,
      incident_date:  r.incident_date,
      link_type:      linkRows.find(
        l => l.incident_a === r.id || l.incident_b === r.id
      )?.link_type ?? 'related',
    }))
  }

  const timeline: SourceTimelineEntry[] = Array.isArray(incident.source_timeline)
    ? incident.source_timeline as SourceTimelineEntry[]
    : []

  const shareUrl    = `${siteUrl}/incidents/${slug}?utm_source=share&utm_medium=share_card&utm_campaign=${incident.classification}`
  const incidentUrl = `${siteUrl}/incidents/${slug}`

  const jsonLd = {
    '@context':    'https://schema.org',
    '@type':       'NewsArticle',
    headline:      incident.title,
    description:   incident.summary.slice(0, 160),
    datePublished: incident.incident_date,
    url:           incidentUrl,
    image:         incident.pixel_art_url ?? `${siteUrl}/og-default.jpg`,
    publisher: {
      '@type': 'Organization',
      name:    'Yishun Again',
      url:     siteUrl,
    },
  }

  return (
    <article className="max-w-2xl mx-auto px-4 py-8 w-full">
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
      />

      {/* Classification + severity + hype */}
      <div className="flex items-center gap-3 mb-4 flex-wrap">
        <span
          className={`text-2xl ${classColor(incident.classification, incident.custom_label)}`}
          title={classTooltip(incident.classification, incident.custom_label)}
        >
          {classIcon(incident.classification, incident.custom_label)}
        </span>
        <span
          className="font-body text-text-secondary"
          style={{ fontSize: '14px' }}
          title={severityTooltip(incident.severity)}
        >
          {severityDiamonds(incident.severity)}
        </span>
        {hypeFromSources(incident.corroboration_count) > 0 && (
          <span
            className="font-body text-amber-lt"
            style={{ fontSize: '14px' }}
            title={HYPE_TOOLTIP}
          >
            {hypeMeter(hypeFromSources(incident.corroboration_count))}
          </span>
        )}
        {incident.is_milestone && (
          <span className="font-body font-bold text-amber-lt border border-amber-lt/50 px-1"
                style={{ fontSize: '14px' }}>
            ⚡ {incident.milestone_type?.replace('_', ' ').toUpperCase()}
          </span>
        )}
      </div>

      {/* Title */}
      <h1 className="font-body font-bold text-text-primary leading-snug mb-2"
          style={{ fontSize: '20px' }}>
        {incident.title}
      </h1>

      {/* Meta */}
      <div className="flex gap-4 flex-wrap font-body text-text-secondary mb-6"
           style={{ fontSize: '14px' }}>
        <span>{fmtDate(incident.incident_date)}</span>
        {incident.area_name && <span>{incident.area_name}</span>}
        {incident.block_number && <span>{incident.block_number}</span>}
        <span>Corroborated by {incident.corroboration_count} source{incident.corroboration_count !== 1 ? 's' : ''}</span>
      </div>

      {/* Pixel art */}
      {incident.pixel_art_url ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={incident.pixel_art_url}
          alt={`Pixel art for: ${incident.title}`}
          className="w-full aspect-video object-cover mb-6 border border-border"
        />
      ) : (
        <div className="w-full aspect-video bg-surface border border-border flex items-center justify-center mb-6">
          <span className="font-body text-text-secondary" style={{ fontSize: '14px' }}>
            PIXEL ART · COMING SOON
          </span>
        </div>
      )}

      {/* Summary */}
      <p className="font-body text-text-primary leading-relaxed mb-6" style={{ fontSize: '16px' }}>
        {incident.summary}
      </p>

      {/* Stats row */}
      {((incident.deaths ?? 0) > 0 || (incident.injuries ?? 0) > 0) && (
        <div className="flex gap-6 font-body text-text-secondary mb-6" style={{ fontSize: '14px' }}>
          {incident.deaths !== null && (
            <span>💀 Deaths: <strong style={{ color: 'var(--color-dark-events)' }}>{incident.deaths}</strong></span>
          )}
          {incident.injuries !== null && (
            <span>🩸 Injuries: <strong className="text-text-primary">{incident.injuries}</strong></span>
          )}
        </div>
      )}

      {/* Source links — dated and sorted earliest-first */}
      {(() => {
        // Build url→date lookup from source_timeline
        const urlDate = new Map<string, string>()
        for (const entry of timeline) {
          if (entry.source_url && !urlDate.has(entry.source_url)) {
            urlDate.set(entry.source_url, entry.date)
          }
        }
        // Sort: URLs with a known date go first (earliest), undated URLs at end
        const sorted = [...(incident.source_urls ?? [])].sort((a, b) => {
          const da = urlDate.get(a) ?? 'zzzz'
          const db = urlDate.get(b) ?? 'zzzz'
          return da.localeCompare(db)
        })
        return (
          <div className="mb-6">
            <div className="font-body text-text-secondary mb-2 uppercase" style={{ fontSize: '14px' }}>
              Sources
            </div>
            <ul className="space-y-2">
              {sorted.map((url, i) => {
                let domain = url
                try { domain = new URL(url).hostname.replace(/^www\./, '') } catch {}
                const date = urlDate.get(url)
                return (
                  <li key={i} className="flex items-baseline gap-2">
                    {date && (
                      <span className="font-body text-text-secondary flex-none" style={{ fontSize: '12px' }}>
                        {fmtDate(date)}
                      </span>
                    )}
                    <a
                      href={url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="font-body text-amber-lt hover:underline break-all"
                      style={{ fontSize: '14px' }}
                    >
                      {domain}
                    </a>
                  </li>
                )
              })}
            </ul>
          </div>
        )
      })()}

      {/* Story timeline — visual horizontal layout. Same-date entries collapse
          to one node; requires 2+ DISTINCT dates to render. */}
      {(() => {
        const nodes = collapseTimelineByDate(timeline)
        if (nodes.length < 2) return null
        const ROLE_LABEL: Record<string, string> = {
          initial:          'REPORTED',
          update:           'UPDATE',
          verdict:          'VERDICT',
          sentencing:       'SENTENCED',
          appeal:           'APPEAL',
          appeal_dismissed: 'APPEAL DISMISSED',
          correction:       'CORRECTED',
          follow_up:        'FOLLOW UP',
        }
        const concludeEntry = lastVerdictEntry(timeline)
        const showTotal = concludeEntry != null && incident.first_reported_at != null
        const totalLabel = verdictNoun(concludeEntry?.role)

        return (
          <details className="mb-6 group" open>
            <summary
              className="list-none flex items-center gap-2 cursor-pointer hover:opacity-80 mb-4"
              style={{ fontFamily: "'Press Start 2P', monospace", fontSize: 10, color: 'var(--color-amber)', letterSpacing: '0.05em' }}
            >
              <span className="group-open:hidden">▶</span>
              <span className="hidden group-open:inline">▼</span>
              STORY TIMELINE
            </summary>

            {/* Horizontal node row */}
            <div style={{ overflowX: 'auto', paddingBottom: 8 }}>
              <div style={{ display: 'flex', alignItems: 'center', minWidth: 'max-content' }}>
                {nodes.map((entry, i) => {
                  const label  = ROLE_LABEL[entry.role ?? 'initial'] ?? 'UPDATE'
                  const gapStr = i > 0
                    ? formatDurationGap(new Date(nodes[i - 1].date), new Date(entry.date))
                    : null
                  return (
                    <div key={i} style={{ display: 'flex', alignItems: 'center' }}>
                      {/* Connector with gap label — only between nodes */}
                      {gapStr && (
                        <div style={{ display: 'flex', alignItems: 'center', minWidth: 90, padding: '0 4px', paddingBottom: 20 }}>
                          <div style={{ flex: 1, height: 1, borderTop: '1px solid var(--color-border)', minWidth: 10 }} />
                          <span style={{ padding: '2px 6px', fontFamily: "'Courier Prime', monospace", fontSize: 10, color: 'var(--color-amber-dim)', whiteSpace: 'nowrap' }}>
                            {gapStr}
                          </span>
                          <div style={{ flex: 1, height: 1, borderTop: '1px solid var(--color-border)', minWidth: 10 }} />
                        </div>
                      )}

                      {/* Node: label / circle / date */}
                      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 5, minWidth: 80 }}>
                        <span style={{ fontFamily: "'Press Start 2P', monospace", fontSize: 8, color: 'var(--color-amber)', textAlign: 'center', whiteSpace: 'nowrap', letterSpacing: '0.05em' }}>
                          {label}
                        </span>
                        <span style={{ color: 'var(--color-amber)', fontSize: 14, lineHeight: 1 }}>●</span>
                        <span style={{ fontFamily: "'Courier Prime', monospace", fontSize: 11, color: 'var(--color-amber)', whiteSpace: 'nowrap' }}>
                          {fmtDate(entry.date)}
                        </span>
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>

            {/* Total duration — verdict/sentencing/appeal stories only */}
            {showTotal && (
              <div style={{ fontFamily: "'Courier Prime', monospace", fontSize: 13, color: 'var(--color-amber)', marginTop: 10 }}>
                ⏱ Total: {formatDuration(new Date(incident.first_reported_at!), new Date(concludeEntry!.date))} from first report to {totalLabel}
              </div>
            )}
          </details>
        )
      })()}

      {/* Confirmed related incidents */}
      {relatedIncidents.length > 0 && (
        <div className="mb-6">
          <div className="font-body text-text-secondary mb-3 uppercase" style={{ fontSize: '14px' }}>
            Related Incidents
          </div>
          <ul className="space-y-2">
            {relatedIncidents.map(rel => (
              <li key={rel.id}>
                <Link
                  href={`/incidents/${rel.slug}`}
                  className="flex items-center gap-3 px-3 py-2 border border-border hover:bg-surface transition-colors group"
                >
                  <span className={`text-base flex-none ${classColor(rel.classification, rel.custom_label)}`}
                        title={classTooltip(rel.classification, rel.custom_label)}>
                    {classIcon(rel.classification, rel.custom_label)}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="font-body font-bold text-text-primary group-hover:text-amber-lt transition-colors line-clamp-1"
                         style={{ fontSize: '14px' }}>
                      {rel.title}
                    </div>
                    <div className="font-body text-text-secondary" style={{ fontSize: '12px' }}>
                      {fmtDate(rel.incident_date)}
                      {' · '}
                      <span className="capitalize">{rel.link_type.replace('_', ' ')}</span>
                    </div>
                  </div>
                  <span className="font-body text-text-secondary flex-none" style={{ fontSize: '14px' }}>→</span>
                </Link>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Share */}
      <div className="flex items-center gap-4 border-t border-border pt-4">
        <ShareButton url={shareUrl} title={incident.title} />
        <Link href="/" className="font-body text-text-secondary hover:text-text-primary"
              style={{ fontSize: '14px' }}>
          ← Back to map
        </Link>
      </div>

      <Suspense fallback={null}>
        <UTMLogger incidentId={incident.id} />
      </Suspense>
    </article>
  )
}
