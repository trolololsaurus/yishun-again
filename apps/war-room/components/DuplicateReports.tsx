import { safeHref } from '@/lib/utils'

interface DuplicateReport {
  source_url?: string
  source_name?: string
  headline?: string
  date?: string
  summary?: string
}

export function DuplicateReports({ reports }: { reports: unknown }) {
  if (!Array.isArray(reports) || reports.length === 0) return null
  const items = reports.filter((report): report is DuplicateReport =>
    Boolean(report && typeof report === 'object'),
  )
  if (!items.length) return null

  return (
    <section className="border border-yellow/40 bg-yellow/5 p-3 space-y-2">
      <h3 className="text-yellow text-xs font-bold uppercase tracking-wide">
        Additional reports matched to this incident ({items.length})
      </h3>
      <p className="text-text-secondary text-xs">
        Compare each report with the draft before approving. The original title and summary were left unchanged.
      </p>
      <ul className="space-y-3">
        {items.map((report, index) => (
          <li key={report.source_url ?? `${report.headline ?? 'report'}-${index}`} className="text-sm">
            <div className="flex flex-wrap gap-x-2 text-xs text-text-secondary">
              {report.source_name && <span>{report.source_name}</span>}
              {report.date && <time>{report.date}</time>}
            </div>
            {report.source_url ? (
              <a
                href={safeHref(report.source_url)}
                target="_blank"
                rel="noopener noreferrer"
                className="text-yellow hover:underline"
              >
                {report.headline || report.source_url}
              </a>
            ) : (
              <p className="text-text-primary">{report.headline || 'Additional report'}</p>
            )}
            {report.summary && <p className="mt-1 text-text-secondary">{report.summary}</p>}
          </li>
        ))}
      </ul>
    </section>
  )
}
