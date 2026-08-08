'use client'

import { useState } from 'react'
import Link  from 'next/link'
import Image from 'next/image'
import {
  classIcon, classColor, classTooltip, HYPE_TOOLTIP,
  severityDiamonds, severityTooltip, hypeMeter, hypeFromSources,
  fmtDate, toParagraphs, uniqueSources, canonicalUrl, dateFromUrl,
} from '@/lib/utils'
import type { Incident } from '@/lib/types'

type Row = Pick<Incident,
  'id' | 'slug' | 'title' | 'summary' | 'classification' | 'custom_label' | 'severity'
  | 'corroboration_count' | 'incident_date' | 'area_name'
  | 'source_urls' | 'source_timeline' | 'pixel_art_url'>

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
    slug, title, summary, classification, custom_label, severity, corroboration_count,
    incident_date, area_name, source_urls, source_timeline, pixel_art_url,
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

          <Link
            href={`/incidents/${slug}`}
            className="font-body text-amber hover:underline"
            style={{ fontSize: 14 }}
          >
            Full page ↗
          </Link>
        </div>
      )}
    </article>
  )
}
