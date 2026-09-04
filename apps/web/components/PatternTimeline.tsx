import Link from 'next/link'
import Image from 'next/image'
import { classIcon, classColor, classTooltip, fmtDate } from '@/lib/utils'
import type { Incident } from '@/lib/types'

type Node = Pick<Incident,
  'id' | 'slug' | 'title' | 'classification' | 'custom_label' | 'incident_date' | 'area_name' | 'pixel_art_url'>

/**
 * Vertical, chronological timeline of a pattern's incidents. A left rail acts as
 * the spine; each row is a small art thumbnail + date + title linking to the
 * incident page. Ordered oldest→newest, so an incident appended to the pattern
 * later just extends the list at the bottom.
 *
 * Server component — no client JS. Thumbnails go through next/image (fill +
 * fixed `sizes`), so mobile pulls a ~112px-wide file, lazily, instead of the
 * full 1200×630 art.
 */
export function PatternTimeline({ incidents }: { incidents: Node[] }) {
  if (incidents.length === 0) return null

  return (
    <ol className="relative border-l border-border ml-1.5 space-y-5">
      {incidents.map(inc => (
        <li key={inc.id} className="relative pl-5">
          {/* Dot on the rail */}
          <span
            aria-hidden
            className="absolute rounded-full bg-amber"
            style={{ left: -5, top: 6, width: 9, height: 9 }}
          />

          <Link href={`/incidents/${inc.slug}`} className="group flex gap-3" title={inc.title}>
            {/* Thumbnail — art if present, neutral box otherwise */}
            <div
              className="relative flex-none overflow-hidden border border-border bg-surface group-hover:border-amber transition-colors"
              style={{ width: 112, height: 63 }}
            >
              {inc.pixel_art_url ? (
                // NEAREST-resampled pixel art (art/generate_image.py) needs the same
                // hint here — otherwise the browser's downscale to 112px smooths its
                // hard edges into a blur.
                <Image src={inc.pixel_art_url} alt="" fill sizes="112px" className="object-cover"
                       style={{ imageRendering: 'pixelated' }} />
              ) : (
                <span className="absolute inset-0 flex items-center justify-center font-body text-text-secondary"
                      style={{ fontSize: 9 }}>
                  NO ART
                </span>
              )}
              {/* Classification marker, bottom-left */}
              <span
                className={`absolute bottom-0.5 left-1 ${classColor(inc.classification, inc.custom_label)}`}
                style={{ fontSize: 13 }}
                title={classTooltip(inc.classification, inc.custom_label)}
              >
                {classIcon(inc.classification, inc.custom_label)}
              </span>
            </div>

            {/* Date + title + area */}
            <div className="min-w-0 flex-1">
              <div className="font-body text-text-secondary" style={{ fontSize: 12 }}>
                {fmtDate(inc.incident_date)}
                {inc.area_name && <> · {inc.area_name}</>}
              </div>
              <div className="font-body font-bold text-text-primary group-hover:text-amber-lt transition-colors line-clamp-2"
                   style={{ fontSize: 15, lineHeight: 1.3 }}>
                {inc.title}
              </div>
            </div>
          </Link>
        </li>
      ))}
    </ol>
  )
}
