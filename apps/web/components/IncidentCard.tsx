import Link from 'next/link'
import { classIcon, classColor, classTooltip, HYPE_TOOLTIP, severityDiamonds, severityTooltip, hypeMeter, hypeFromSources, fmtDate, formatDuration, lastVerdictEntry, verdictNoun } from '@/lib/utils'
import type { Incident } from '@/lib/types'

interface Props {
  incident: Pick<Incident,
    'slug' | 'title' | 'classification' | 'custom_label' | 'severity' | 'corroboration_count'
    | 'published_at' | 'incident_date' | 'area_name' | 'is_milestone'
    | 'is_developing' | 'update_count' | 'first_reported_at'
    | 'source_urls' | 'source_timeline' | 'latest_source_role'
  >
  style?: React.CSSProperties  // passed from react-window
}

export function IncidentCard({ incident, style }: Props) {
  const {
    slug, title, classification, custom_label, severity, corroboration_count, published_at, incident_date,
    area_name, is_milestone, is_developing, update_count, first_reported_at,
    source_urls, source_timeline, latest_source_role,
  } = incident

  // Count the SAME array the detail page lists under "Sources", so the feed and
  // the incident can never disagree. corroboration_count is the fallback for
  // any caller that did not select source_urls.
  const sourceCount = Array.isArray(source_urls)
    ? new Set(source_urls.filter(Boolean)).size
    : (corroboration_count ?? 1)

  // Lightning bolts grow with corroboration: 2 sources → ⚡, 3 → ⚡⚡, etc.
  const lightning = hypeFromSources(sourceCount)

  // The real conclusion date lives in source_timeline (verdict/sentencing/
  // appeal entry) — NOT incident_date, which is the event date and equals
  // first_reported_at for most rows (the old "1 day to verdict" bug).
  const vEntry      = lastVerdictEntry(source_timeline)
  const verdictDate = vEntry?.date ?? incident_date
  const isVerdict   = vEntry != null || latest_source_role === 'verdict'

  // Time from first report to verdict, e.g. "2 years 5 months to verdict"
  const verdictDuration = vEntry != null && first_reported_at != null
    ? `${formatDuration(new Date(first_reported_at), new Date(verdictDate))} to ${verdictNoun(vEntry.role)}`
    : null

  const reportCount = (update_count ?? 0) + 1

  return (
    <Link
      href={`/incidents/${slug}`}
      style={{ ...style, borderBottom: '1px solid var(--color-border)' }}
      className={[
        'group flex gap-3 px-4 py-3 hover:bg-[#0F1A2E] transition-colors block',
        'min-h-[48px]',
        is_developing ? 'border-l-2 border-l-amber' : '',
      ].join(' ')}
    >
      {/* Classification icon */}
      <span
        className={`text-base flex-none mt-0.5 ${classColor(classification, custom_label)}`}
        title={classTooltip(classification, custom_label)}
      >
        {classIcon(classification, custom_label)}
      </span>

      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 mb-1 flex-wrap">
          {is_milestone && (
            <span className="font-display border border-amber-lt/50 px-1.5 py-0.5 text-amber-lt"
                  style={{ fontSize: 9, letterSpacing: '0.05em' }}>
              ⚡ MILESTONE
            </span>
          )}
          <span className="font-body text-text-secondary" style={{ fontSize: '14px' }}>
            <span title={severityTooltip(severity)}>{severityDiamonds(severity)}</span>
            {lightning > 0 && <> <span title={HYPE_TOOLTIP}>{hypeMeter(lightning)}</span></>}
          </span>
          {/* The count in words, not just bolts to be counted by eye — this is
              the number the incident page prints under "Sources". */}
          <span className="font-body text-text-secondary" style={{ fontSize: 12 }} title={HYPE_TOOLTIP}>
            {sourceCount} source{sourceCount !== 1 ? 's' : ''}
          </span>
        </div>

        <p className="font-body font-bold text-text-primary leading-snug line-clamp-2 group-hover:text-amber transition-colors"
           style={{ fontSize: 16 }}>
          {title}
        </p>

        <div className="flex gap-3 mt-1 font-body flex-wrap" style={{ fontSize: 13, color: 'var(--color-text-secondary)' }}>
          {is_developing ? (
            <>
              <span className="text-amber">{reportCount} report{reportCount !== 1 ? 's' : ''}</span>
              {first_reported_at && (
                <span>First reported {fmtDate(first_reported_at)}</span>
              )}
            </>
          ) : (
            <>
              {!isVerdict && <span>{fmtDate(incident_date)}</span>}
              {area_name && <span className="truncate">{area_name}</span>}
            </>
          )}
        </div>

        {/* Combined first-reported → verdict line (verdict stories only) */}
        {isVerdict && (
          <div className="mt-0.5 font-body truncate" style={{ fontSize: 12 }}>
            {first_reported_at && (
              <>
                <span style={{ color: '#7A8BAA' }}>First reported: </span>
                <span style={{ color: '#805828' }}>{fmtDate(first_reported_at)}</span>
                {verdictDuration && (
                  <>
                    <span style={{ color: '#3D4F6A' }}> · </span>
                    <span style={{ color: '#805828' }}>{verdictDuration}</span>
                  </>
                )}
                <span style={{ color: '#3D4F6A' }}> · </span>
              </>
            )}
            <span style={{ color: '#7A8BAA' }}>{vEntry ? `${verdictNoun(vEntry.role)[0].toUpperCase()}${verdictNoun(vEntry.role).slice(1)}` : 'Verdict'}: </span>
            <span style={{ color: '#4ECDC4' }}>{fmtDate(verdictDate)}</span>
          </div>
        )}
      </div>
    </Link>
  )
}
