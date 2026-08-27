'use client'

import { useState } from 'react'
import Link  from 'next/link'
import Image from 'next/image'
import {
  classIcon, classColor, classTooltip, HYPE_TOOLTIP,
  severityDiamonds, severityTooltip, hypeMeter, hypeFromSources,
  fmtDate, toParagraphs, uniqueSources, canonicalUrl, dateFromUrl,
  collapseTimelineByDate, formatDurationGap,
} from '@/lib/utils'
import type { Incident } from '@/lib/types'
import { SITE_URL } from '@/lib/site'
import { ShareButton } from '@/app/incidents/[slug]/ShareButton'

type Row = Pick<Incident,
  'id' | 'slug' | 'title' | 'summary' | 'classification' | 'custom_label' | 'severity'
  | 'deaths' | 'injuries' | 'corroboration_count' | 'incident_date' | 'area_name' | 'block_number'
  | 'source_urls' | 'source_timeline' | 'pixel_art_url'>

// Story-timeline role → short pixel-font label, matching the detail page.
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

/**
 * News-article card for the NEWS FEED (`/`): a banner image on top, a slightly
 * larger headline, a meta row (classification emoji + severity + sources + date),
 * and a 3–4 line teaser. Clicking expands the card IN PLACE to the full write-up
 * + dated source links + a link to the standalone page — no navigation. Each
 * card owns its own open state, so several can be open at once.
 *
 * The classification lives in the meta row, not on the image (no overlay box).
 * The timeline keeps the compact `IncidentCard`; this is feed-only.
 */
export function NewsCard({ incident }: { incident: Row }) {
  const {
    id, slug, title, summary, classification, custom_label, severity, deaths, injuries, corroboration_count,
    incident_date, area_name, block_number, source_urls, source_timeline, pixel_art_url,
  } = incident
  const [expanded, setExpanded] = useState(false)

  // Count the same array the detail page lists, collapsing tracking-param dupes.
  const sources     = Array.isArray(source_urls) ? uniqueSources(source_urls) : []
  const sourceCount = sources.length || (corroboration_count ?? 1)
  const lightning   = hypeFromSources(sourceCount)
  const paras       = toParagraphs(summary)

  // Source publication dates — the source_timeline entry (canonical-keyed) then
  // a date the publisher stamped into the URL path; neither → "Undated".
  const urlDate = new Map<string, string>()
  for (const e of source_timeline ?? []) {
    if (e.source_url && e.date) {
      const k = canonicalUrl(e.source_url)
      if (!urlDate.has(k)) urlDate.set(k, e.date)
    }
  }
  const dateFor = (u: string): string | null => urlDate.get(canonicalUrl(u)) ?? dateFromUrl(u)
  const sortedSources = [...sources].sort((a, b) =>
    (dateFor(a) ?? 'zzzz').localeCompare(dateFor(b) ?? 'zzzz')
  )

  const hasCasualties = (deaths ?? 0) > 0 || (injuries ?? 0) > 0
  // Story timeline collapses same-date entries; it needs 2+ distinct dates.
  const timelineNodes = collapseTimelineByDate(source_timeline ?? [])

  return (
    <article className="border-b border-border">
      <button
        type="button"
        onClick={() => setExpanded(v => !v)}
        aria-expanded={expanded}
        className="w-full text-left block group hover:bg-[#0F1A2E] transition-colors"
      >
        {/* Banner — full card width, at the incident-art aspect ratio (40:21 =
            1200×630) so it isn't cropped. Rows with no art get no banner. */}
        {pixel_art_url && (
          <div className="relative w-full" style={{ aspectRatio: '40 / 21' }}>
            <Image
              src={pixel_art_url}
              alt=""
              fill
              sizes="(min-width: 768px) 60vw, 100vw"
              className="object-cover"
            />
          </div>
        )}

        <div className="px-4 py-3">
          {/* Meta row — classification emoji lives here, not on the image. */}
          <div className="flex items-center gap-2 flex-wrap font-body text-text-secondary" style={{ fontSize: 14 }}>
            <span
              className={classColor(classification, custom_label)}
              style={{ fontSize: 15 }}
              title={classTooltip(classification, custom_label)}
            >
              {classIcon(classification, custom_label)}
            </span>
            <span title={severityTooltip(severity)}>{severityDiamonds(severity)}</span>
            {lightning > 0 && <span title={HYPE_TOOLTIP}>{hypeMeter(lightning)}</span>}
            <span title={HYPE_TOOLTIP}>{sourceCount} source{sourceCount !== 1 ? 's' : ''}</span>
            <span aria-hidden>·</span>
            <span>{fmtDate(incident_date)}</span>
            {area_name && <><span aria-hidden>·</span><span className="truncate">{area_name}</span></>}
            {block_number && <><span aria-hidden>·</span><span className="truncate">{block_number}</span></>}
          </div>

          <h2
            className="font-body font-bold text-text-primary leading-tight mt-2 line-clamp-3 group-hover:text-amber transition-colors"
            style={{ fontSize: 23 }}
          >
            {title}
          </h2>

          {!expanded && paras.length > 0 && (
            <p className="font-body text-text-secondary mt-1.5 line-clamp-4" style={{ fontSize: 14, lineHeight: 1.5 }}>
              {paras[0]}
            </p>
          )}

          <span
            className="inline-block font-display mt-2"
            style={{ fontSize: 10, letterSpacing: '0.1em', color: 'var(--color-amber)' }}
          >
            {expanded ? 'SHOW LESS ▴' : 'READ MORE ▾'}
          </span>
        </div>
      </button>

      {/* Expanded — full write-up + dated sources + the standalone page. Lives
          OUTSIDE the toggle button so its links aren't swallowed by the toggle. */}
      {expanded && (
        <div className="px-4 pb-4">
          <div className="mb-4">
            {paras.map((p, i) => (
              <p
                key={i}
                className="font-body text-text-primary leading-relaxed"
                style={{ fontSize: 16, marginTop: i === 0 ? 0 : '0.9em' }}
              >
                {p}
              </p>
            ))}
          </div>

          {/* Casualties */}
          {hasCasualties && (
            <div className="flex gap-6 font-body text-text-secondary mb-4" style={{ fontSize: 14 }}>
              {(deaths ?? 0) > 0 && (
                <span>💀 Deaths: <strong style={{ color: 'var(--color-dark-events)' }}>{deaths}</strong></span>
              )}
              {(injuries ?? 0) > 0 && (
                <span>🩸 Injuries: <strong className="text-text-primary">{injuries}</strong></span>
              )}
            </div>
          )}

          {sortedSources.length > 0 && (
            <div className="mb-3">
              <div className="font-body text-text-secondary mb-2 uppercase" style={{ fontSize: 14 }}>
                Sources ({sourceCount})
              </div>
              <ul className="space-y-1.5">
                {sortedSources.map((u, i) => {
                  let domain = u
                  try { domain = new URL(u).hostname.replace(/^www\./, '') } catch { /* keep raw */ }
                  const d = dateFor(u)
                  return (
                    <li key={i} className="flex items-baseline gap-2">
                      <span
                        className="font-body text-text-secondary flex-none tabular-nums"
                        style={{ fontSize: 12, minWidth: '9ch' }}
                        title={d ? 'Article publication date' : 'Publication date not recorded for this link'}
                      >
                        {d ? fmtDate(d) : 'Undated'}
                      </span>
                      <a
                        href={u}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="font-body text-amber-lt hover:underline break-all"
                        style={{ fontSize: 14 }}
                      >
                        {domain}
                      </a>
                    </li>
                  )
                })}
              </ul>
            </div>
          )}

          {/* Story timeline — same horizontal layout as the detail page; needs
              2+ distinct dates. source_timeline is already in the feed row, so
              no extra fetch. */}
          {timelineNodes.length >= 2 && (
            <div className="mb-4">
              <div className="font-body text-text-secondary mb-3 uppercase" style={{ fontSize: 14 }}>
                Story Timeline
              </div>
              <div style={{ overflowX: 'auto', paddingBottom: 8 }}>
                <div style={{ display: 'flex', alignItems: 'center', minWidth: 'max-content' }}>
                  {timelineNodes.map((entry, i) => {
                    const label  = ROLE_LABEL[entry.role ?? 'initial'] ?? 'UPDATE'
                    const gapStr = i > 0
                      ? formatDurationGap(new Date(timelineNodes[i - 1].date), new Date(entry.date))
                      : null
                    return (
                      <div key={i} style={{ display: 'flex', alignItems: 'center' }}>
                        {gapStr && (
                          <div style={{ display: 'flex', alignItems: 'center', minWidth: 90, padding: '0 4px', paddingBottom: 20 }}>
                            <div style={{ flex: 1, height: 1, borderTop: '1px solid var(--color-border)', minWidth: 10 }} />
                            <span style={{ padding: '2px 6px', fontFamily: "'Courier Prime', monospace", fontSize: 10, color: 'var(--color-amber-dim)', whiteSpace: 'nowrap' }}>
                              {gapStr}
                            </span>
                            <div style={{ flex: 1, height: 1, borderTop: '1px solid var(--color-border)', minWidth: 10 }} />
                          </div>
                        )}
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
            </div>
          )}

          <div className="flex items-center gap-4">
            <Link
              href={`/incidents/${slug}`}
              className="font-body text-amber hover:underline"
              style={{ fontSize: 14 }}
            >
              Full page ↗
            </Link>
            <ShareButton url={`${SITE_URL}/incidents/${slug}`} title={title} incidentId={id} />
          </div>
        </div>
      )}
    </article>
  )
}
