import { notFound } from 'next/navigation'
import Link from 'next/link'
import { supabase } from '@/lib/supabase'
import { classIcon, classLabel, classColor, severityDiamonds, hypeMeter, safeHref, toParagraphs } from '@/lib/utils'

// Operator-only draft/live preview. Uses the secret-key client (bypasses RLS),
// so unpublished drafts render here — unlike the public site, which 404s them.
// Always fresh; never cache a draft.
export const dynamic = 'force-dynamic'

interface Props { params: Promise<{ slug: string }> }

interface TimelineEntry {
  date?: string; role?: string; source_url?: string; source_name?: string; headline?: string
}

function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString('en-SG', { day: '2-digit', month: 'short', year: 'numeric' })
}

function host(u: string): string {
  try { return new URL(u).hostname.replace(/^www\./, '') } catch { return u }
}

export default async function IncidentPreview({ params }: Props) {
  const slug = (await params).slug.replace(/[^a-z0-9-]/g, '')
  if (!slug) notFound()

  const { data, error } = await supabase
    .from('incidents')
    .select('*')
    .eq('slug', slug)
    .single()

  if (error || !data) notFound()
  const inc = data as Record<string, any>
  const timeline: TimelineEntry[] = Array.isArray(inc.source_timeline) ? inc.source_timeline : []

  return (
    <article className="max-w-3xl font-body">
      <Link href="/incidents" className="text-text-secondary hover:text-text-primary text-sm">← Incidents</Link>

      {/* Status banner */}
      <div className="mt-4 mb-6 flex items-center gap-3 text-sm">
        {inc.is_published
          ? <span className="text-green font-bold">● LIVE</span>
          : <span className="text-yellow font-bold">● DRAFT (not on public site)</span>}
        {inc.is_published && (
          <a
            href={`https://www.yishunagain.com/incidents/${inc.slug}`}
            target="_blank" rel="noopener noreferrer"
            className="text-yellow hover:underline"
          >
            Open public page ↗
          </a>
        )}
      </div>

      {/* Classification + severity + hype */}
      <div className="flex items-center gap-3 mb-3 text-sm">
        <span className={classColor(inc.classification, inc.custom_label)}>
          {classIcon(inc.classification, inc.custom_label)} {classLabel(inc.classification, inc.custom_label)}
        </span>
        <span className="text-text-secondary">{severityDiamonds(inc.severity)}</span>
        {(inc.corroboration_count ?? 1) > 1 && (
          <span className="text-yellow">{hypeMeter((inc.corroboration_count ?? 1) - 1)}</span>
        )}
      </div>

      <h1 className="font-bold text-text-primary text-xl leading-snug mb-2">{inc.title}</h1>

      <div className="flex gap-4 flex-wrap text-text-secondary text-sm mb-6">
        <span>Event: {fmtDate(inc.incident_date)}</span>
        {inc.first_reported_at && <span>First reported: {fmtDate(inc.first_reported_at)}</span>}
        <span>Published: {fmtDate(inc.published_at)}</span>
        {inc.area_name && <span>{inc.area_name}</span>}
        {inc.block_number && <span>Blk {inc.block_number}</span>}
        <span>{inc.corroboration_count ?? 0} source{(inc.corroboration_count ?? 0) !== 1 ? 's' : ''}</span>
      </div>

      {inc.pixel_art_url ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={inc.pixel_art_url} alt="" className="w-full aspect-video object-cover mb-6 border border-border" />
      ) : (
        <div className="w-full aspect-video bg-surface border border-border flex items-center justify-center mb-6 text-text-secondary text-sm">
          NO PIXEL ART
        </div>
      )}

      {/* Paragraphed exactly as apps/web renders it, so what the operator
          reviews here is what ships. `whitespace-pre-wrap` on the raw string
          showed an updated summary as one wall of text while the public page
          split it — the review surface disagreed with production. */}
      <div className="mb-6 space-y-4">
        {toParagraphs(inc.summary).map((para, i) => (
          <p key={i} className="text-text-primary leading-relaxed text-base">{para}</p>
        ))}
      </div>

      {((inc.deaths ?? 0) > 0 || (inc.injuries ?? 0) > 0) && (
        <div className="flex gap-6 text-text-secondary text-sm mb-6">
          {inc.deaths != null && <span>💀 Deaths: <strong className="text-red">{inc.deaths}</strong></span>}
          {inc.injuries != null && <span>🩸 Injuries: <strong className="text-text-primary">{inc.injuries}</strong></span>}
        </div>
      )}

      {/* Sources */}
      <div className="mb-6">
        <div className="text-text-secondary uppercase text-xs mb-2">Sources</div>
        <ul className="space-y-1">
          {(inc.source_urls ?? []).map((u: string, i: number) => (
            <li key={i} className="text-sm break-all">
              <a href={safeHref(u)} target="_blank" rel="noopener noreferrer" className="text-yellow hover:underline">{host(u)}</a>
            </li>
          ))}
        </ul>
      </div>

      {/* Timeline */}
      {timeline.length > 0 && (
        <div className="mb-6">
          <div className="text-text-secondary uppercase text-xs mb-2">Story timeline</div>
          <ul className="space-y-1 text-sm">
            {timeline.map((e, i) => (
              <li key={i} className="text-text-secondary">
                <span className="text-text-primary">{fmtDate(e.date)}</span>
                {e.role && <span className="text-yellow"> · {e.role}</span>}
                {e.source_name && <span> · {e.source_name}</span>}
                {e.headline && <span className="text-text-secondary"> — {e.headline}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </article>
  )
}
