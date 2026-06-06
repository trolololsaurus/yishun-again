import Link from 'next/link'
import { CLASS_ICON, CLASS_COLOR, CLASS_TOOLTIP, HYPE_TOOLTIP, severityDiamonds, severityTooltip, hypeMeter, fmtDate, formatDuration } from '@/lib/utils'
import type { Incident } from '@/lib/types'

interface Props {
  incident: Pick<Incident,
    'slug' | 'title' | 'classification' | 'severity' | 'hype_meter'
    | 'published_at' | 'incident_date' | 'area_name' | 'is_milestone'
    | 'is_developing' | 'update_count' | 'first_reported_at'
    | 'source_timeline' | 'latest_source_role'
  >
  style?: React.CSSProperties  // passed from react-window
}

export function IncidentCard({ incident, style }: Props) {
  const {
    slug, title, classification, severity, hype_meter, published_at, incident_date,
    area_name, is_milestone, is_developing, update_count, first_reported_at,
    source_timeline, latest_source_role,
  } = incident

  // Duration line: only for concluded (verdict) stories with 2+ timeline entries
  const showDuration =
    latest_source_role === 'verdict' &&
    Array.isArray(source_timeline) && source_timeline.length >= 2 &&
    first_reported_at != null

  // Time from first report to verdict, e.g. "2 years 5 months to verdict"
  const verdictDuration = showDuration
    ? `${formatDuration(new Date(first_reported_at!), new Date(incident_date))} to verdict`
    : null

  const isVerdict = latest_source_role === 'verdict'

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
        className={`text-base flex-none mt-0.5 ${CLASS_COLOR[classification] ?? ''}`}
        title={CLASS_TOOLTIP[classification]}
      >
        {CLASS_ICON[classification]}
      </span>

      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 mb-1 flex-wrap">
          {is_developing && (
            <span className="font-display px-1.5 py-0.5"
                  style={{ fontSize: 9, background: 'var(--color-amber-dim)', color: 'var(--color-amber)', letterSpacing: '0.05em' }}>
              DEVELOPING
            </span>
          )}
          {is_milestone && (
            <span className="font-display border border-amber-lt/50 px-1.5 py-0.5 text-amber-lt"
                  style={{ fontSize: 9, letterSpacing: '0.05em' }}>
              ⚡ MILESTONE
            </span>
          )}
          <span className="font-body text-text-secondary" style={{ fontSize: '14px' }}>
            <span title={severityTooltip(severity)}>{severityDiamonds(severity)}</span>
            {hype_meter > 0 && <> <span title={HYPE_TOOLTIP}>{hypeMeter(hype_meter)}</span></>}
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
              {!isVerdict && <span>{fmtDate(published_at)}</span>}
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
            <span style={{ color: '#7A8BAA' }}>Verdict: </span>
            <span style={{ color: '#4ECDC4' }}>{fmtDate(incident_date)}</span>
          </div>
        )}
      </div>
    </Link>
  )
}
