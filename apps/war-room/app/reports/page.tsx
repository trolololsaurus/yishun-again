import Link from 'next/link'
import { supabase } from '@/lib/supabase'
import type { MonthlyReport } from '@/lib/types'

export const revalidate = 0

function fmtPeriod(start: string, end: string): string {
  const opts = { timeZone: 'Asia/Singapore', dateStyle: 'medium' } as const
  return `${new Date(start).toLocaleString('en-SG', opts)} – ${new Date(end).toLocaleString('en-SG', opts)}`
}

function fmtStamp(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('en-SG', {
    timeZone:  'Asia/Singapore',
    dateStyle: 'short',
    timeStyle: 'short',
  })
}

function fmtNum(value: number | null | undefined): string {
  return typeof value === 'number' ? String(value) : '—'
}

const VERDICT_CLS: Record<string, string> = {
  learning:          'text-green',
  stagnant:          'text-yellow',
  regressing:        'text-red',
  insufficient_data: 'text-text-secondary',
}

export default async function ReportsPage() {
  const { data, error } = await supabase
    .from('monthly_reports')
    .select('*')
    .order('period_start', { ascending: false })
    .limit(48)

  if (error) {
    return (
      <div>
        <h1 className="font-bold text-yellow text-lg mb-6">MONTHLY REPORTS</h1>
        <div className="text-red text-sm">
          Failed to load monthly reports: {error.message}
        </div>
      </div>
    )
  }

  const reports = (data ?? []) as MonthlyReport[]

  if (reports.length === 0) {
    return (
      <div>
        <h1 className="font-bold text-yellow text-lg mb-6">MONTHLY REPORTS</h1>
        <div className="border border-border bg-surface p-6 max-w-2xl">
          <div className="text-text-primary text-sm mb-2">No reports yet.</div>
          <div className="text-text-secondary text-sm leading-relaxed">
            The orchestrator generates one report on the 1st of each month, covering the
            previous 30 days of ingestion, publishing, operator workload, learning and
            backend health. It is sent to the operator via Telegram and archived here.
          </div>
        </div>
      </div>
    )
  }

  return (
    <div>
      <div className="flex items-baseline gap-4 mb-2">
        <h1 className="font-bold text-yellow text-lg">MONTHLY REPORTS</h1>
        <span className="text-text-secondary text-sm">
          {reports.length} archived
        </span>
      </div>
      <p className="text-text-secondary text-sm mb-6">
        Generated on the 1st of each month, covering the previous 30 days. Newest first.
      </p>

      <div className="overflow-x-auto">
        <table className="w-full text-sm border-collapse">
          <thead>
            <tr className="border-b border-border text-text-secondary text-xs">
              <th className="text-left py-2 pr-4 uppercase tracking-widest">Period</th>
              <th className="text-right py-2 pr-4 uppercase tracking-widest">Published</th>
              <th className="text-right py-2 pr-4 uppercase tracking-widest">Auto</th>
              <th className="text-right py-2 pr-4 uppercase tracking-widest">Reviews saved</th>
              <th className="text-right py-2 pr-4 uppercase tracking-widest">Queued</th>
              <th className="text-left py-2 pr-4 uppercase tracking-widest">Learning</th>
              <th className="text-left py-2 pr-4 uppercase tracking-widest">Generated (SGT)</th>
              <th className="text-left py-2 uppercase tracking-widest">Sent</th>
            </tr>
          </thead>
          <tbody>
            {reports.map(r => {
              const body      = r.report ?? {}
              const verdict   = body.learning?.verdict ?? '—'
              const gaps      = body.warnings?.length ?? 0
              return (
                <tr key={r.id} className="border-b border-border hover:bg-surface transition-colors">
                  <td className="py-2 pr-4">
                    <Link href={`/reports/${r.id}`} className="text-yellow hover:underline">
                      {fmtPeriod(r.period_start, r.period_end)}
                    </Link>
                    {gaps > 0 && (
                      <div className="text-text-secondary mt-1 text-xs">
                        {gaps} data gap{gaps === 1 ? '' : 's'}
                      </div>
                    )}
                  </td>
                  <td className="py-2 pr-4 text-right text-text-primary">
                    {fmtNum(body.publishing?.published)}
                  </td>
                  <td className="py-2 pr-4 text-right text-text-primary">
                    {fmtNum(body.publishing?.auto_published)}
                  </td>
                  <td className="py-2 pr-4 text-right text-text-primary">
                    {fmtNum(body.operator?.net_reviews_saved)}
                  </td>
                  <td className="py-2 pr-4 text-right text-text-secondary">
                    {fmtNum(body.ingestion?.total_queued)}
                  </td>
                  <td className={`py-2 pr-4 ${VERDICT_CLS[verdict] ?? 'text-text-secondary'}`}>
                    {verdict.toUpperCase()}
                  </td>
                  <td className="py-2 pr-4 text-text-secondary">{fmtStamp(r.created_at)}</td>
                  <td className="py-2">
                    {r.emailed_at
                      ? <span className="text-green">● sent</span>
                      : <span className="text-text-secondary">● not sent</span>}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
