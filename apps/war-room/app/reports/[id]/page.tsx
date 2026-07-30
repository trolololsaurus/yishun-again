import Link from 'next/link'
import { notFound } from 'next/navigation'
import { supabase } from '@/lib/supabase'
import { classColor, classIcon, classLabel, severityDiamonds, validateUUID } from '@/lib/utils'
import type { MonthlyReport, ReportSection } from '@/lib/types'

export const revalidate = 0

interface Props { params: Promise<{ id: string }> }

function fmtDay(value: string | null | undefined): string {
  if (!value) return '—'
  return new Date(value).toLocaleString('en-SG', { timeZone: 'Asia/Singapore', dateStyle: 'medium' })
}

function fmtStamp(value: string | null | undefined): string {
  if (!value) return '—'
  return new Date(value).toLocaleString('en-SG', {
    timeZone:  'Asia/Singapore',
    dateStyle: 'short',
    timeStyle: 'short',
  })
}

function fmtPct(value: number | null | undefined): string {
  return typeof value === 'number' ? `${Math.round(value * 100)}%` : '—'
}

function fmtNum(value: number | null | undefined): string {
  return typeof value === 'number' ? String(value) : '—'
}

function fmtMs(ms: number | null | undefined): string {
  if (ms == null) return '—'
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`
}

function signed(n: number): string {
  return n > 0 ? `+${n}` : String(n)
}

// A section the agent could not read renders as an explicit gap. Zero would be
// a lie: it would say "nothing happened" when the truth is "we cannot tell".
function gapOf(section: ReportSection | undefined): string | null {
  if (!section) return 'No data for this period.'
  if (section.available === false) {
    return `No data for this period${section.reason ? ` — ${section.reason}` : ''}.`
  }
  return null
}

const STATUS_CLS: Record<string, string> = {
  ok:       'text-green',
  degraded: 'text-yellow',
  down:     'text-red',
  unknown:  'text-text-secondary',
}

const VERDICT_CLS: Record<string, string> = {
  learning:          'text-green',
  stagnant:          'text-yellow',
  regressing:        'text-red',
  insufficient_data: 'text-text-secondary',
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h2 className="font-body text-text-secondary text-sm uppercase tracking-widest mb-4">{title}</h2>
      {children}
    </section>
  )
}

function Gap({ text }: { text: string }) {
  return <div className="font-body text-text-secondary text-sm">{text}</div>
}

function Tile({ value, label, sub, tone }: {
  value: string | number
  label: string
  sub?:  string | null
  tone?: string
}) {
  return (
    <div className="bg-surface border border-border px-6 py-4 text-center">
      <div className={`font-body font-bold text-2xl ${tone ?? 'text-text-primary'}`}>{value}</div>
      <div className="font-body text-text-secondary text-sm mt-1">{label}</div>
      {sub && (
        <div className="font-body text-text-secondary mt-1" style={{ fontSize: '10px' }}>{sub}</div>
      )}
    </div>
  )
}

export default async function MonthlyReportPage(props: Props) {
  const params = await props.params
  const id = validateUUID(params.id)
  if (!id) notFound()

  const { data, error } = await supabase
    .from('monthly_reports')
    .select('*')
    .eq('id', id)
    .single()

  if (error || !data) notFound()

  const row  = data as MonthlyReport
  const body = row.report ?? {}
  const { ingestion, publishing, operator, learning, reliability, health, notifications } = body
  const changes = body.changes ?? {}
  const prev    = body.previous_period ?? {}

  const vs = (key: string): string | null =>
    typeof changes[key] === 'number' ? `${signed(changes[key])} vs previous 30 days` : null

  return (
    <div className="space-y-10">
      <div>
        <Link href="/reports" className="font-body text-text-secondary hover:text-text-primary text-sm">
          ← Reports
        </Link>
        <div className="flex items-baseline gap-4 mt-4 mb-2 flex-wrap">
          <h1 className="font-body font-bold text-yellow text-lg">MONTHLY REPORT</h1>
          <span className="font-body text-text-secondary text-sm">
            {fmtDay(row.period_start)} – {fmtDay(row.period_end)}
          </span>
        </div>
        <div className="font-body text-text-secondary text-sm">
          Generated {fmtStamp(row.created_at)} SGT
          {body.period?.trigger ? ` · ${body.period.trigger}` : ''}
          {' · '}
          {row.emailed_at ? `emailed ${fmtStamp(row.emailed_at)} SGT` : 'not emailed'}
        </div>
      </div>

      {/* The 30-second read, exactly as it was emailed */}
      <Section title="Summary">
        <div className="bg-surface border border-border p-6 overflow-x-auto">
          <pre className="font-body text-sm text-text-primary whitespace-pre-wrap leading-relaxed">
            {row.summary_text}
          </pre>
        </div>
      </Section>

      <Section title="Publishing">
        {gapOf(publishing)
          ? <Gap text={gapOf(publishing) as string} />
          : (
            <>
              <div className="flex gap-6 flex-wrap mb-4">
                <Tile value={fmtNum(publishing?.published)} label="published"
                      sub={vs('published')} />
                <Tile value={fmtNum(publishing?.auto_published)} label="auto-published"
                      tone="text-green" sub={vs('auto_published')} />
                <Tile value={fmtNum(publishing?.operator_approved)} label="operator approved" />
                <Tile value={fmtPct(publishing?.auto_share)} label="autonomous share"
                      tone="text-yellow" />
                <Tile value={publishing?.mean_severity ?? '—'} label="mean severity" />
              </div>

              {publishing?.split_available === false && (
                <div className="font-body text-text-secondary text-sm mb-4">
                  training_signals was unreadable — the auto/operator split above is incomplete.
                </div>
              )}

              {(publishing?.published ?? 0) === 0
                ? <Gap text="Nothing was published in this period." />
                : (
                  <>
                    <div className="flex gap-6 flex-wrap font-body text-sm mb-4">
                      {Object.entries(publishing?.by_classification ?? {}).map(([cls, n]) => (
                        <span key={cls} className={classColor(cls)}>
                          {classIcon(cls)} {classLabel(cls)} {n}
                        </span>
                      ))}
                    </div>
                    <div className="flex gap-6 flex-wrap font-body text-sm mb-6 text-text-secondary">
                      {Object.entries(publishing?.by_severity ?? {}).map(([sev, n]) => (
                        <span key={sev}>{severityDiamonds(Number(sev))} ×{n}</span>
                      ))}
                    </div>

                    <div className="overflow-x-auto">
                      <table className="w-full font-body text-sm border-collapse">
                        <thead>
                          <tr className="border-b border-border text-text-secondary" style={{ fontSize: '10px' }}>
                            <th className="text-left py-2 pr-4 uppercase tracking-widest">Incident</th>
                            <th className="text-left py-2 pr-4 uppercase tracking-widest">Type</th>
                            <th className="text-left py-2 pr-4 uppercase tracking-widest">Severity</th>
                            <th className="text-left py-2 pr-4 uppercase tracking-widest">Published (SGT)</th>
                            <th className="text-left py-2 uppercase tracking-widest">Route</th>
                          </tr>
                        </thead>
                        <tbody>
                          {(publishing?.recent ?? []).map((inc, i) => (
                            <tr key={inc.slug ?? i} className="border-b border-border hover:bg-surface transition-colors">
                              <td className="py-2 pr-4 text-text-primary">
                                {inc.slug
                                  ? <Link href={`/incidents/${inc.slug}`} className="hover:text-yellow hover:underline">
                                      {inc.title}
                                    </Link>
                                  : inc.title}
                              </td>
                              <td className={`py-2 pr-4 ${classColor(inc.classification ?? '')}`}>
                                {classLabel(inc.classification ?? '')}
                              </td>
                              <td className="py-2 pr-4 text-text-secondary">
                                {severityDiamonds(inc.severity ?? 0)}
                              </td>
                              <td className="py-2 pr-4 text-text-secondary">{fmtStamp(inc.published_at)}</td>
                              <td className="py-2">
                                {inc.auto
                                  ? <span className="text-green">agent</span>
                                  : <span className="text-text-secondary">operator</span>}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </>
                )}
            </>
          )}
      </Section>

      <Section title="Operator workload">
        {gapOf(operator)
          ? <Gap text={gapOf(operator) as string} />
          : (
            <>
              <div className="flex gap-6 flex-wrap mb-4">
                <Tile value={fmtNum(operator?.net_reviews_saved)} label="reviews avoided"
                      tone="text-green"
                      sub={`~${operator?.minutes_saved ?? 0} min at ${operator?.minutes_per_review ?? 0} min/card`} />
                <Tile value={fmtNum(operator?.operator_decisions)} label="your decisions"
                      sub={vs('operator_decisions')} />
                <Tile value={fmtPct(operator?.autonomy_share)} label="decided by agent"
                      tone="text-yellow" />
                <Tile value={fmtNum(operator?.reverted)} label="auto-publish reverted"
                      tone={(operator?.reverted ?? 0) > 0 ? 'text-red' : undefined} />
                <Tile value={fmtNum(operator?.unpublish)} label="unpublished" />
              </div>

              {Object.keys(operator?.by_action ?? {}).length === 0
                ? <Gap text="No operator or agent decisions in this period." />
                : (
                  <div className="overflow-x-auto">
                    <table className="w-full font-body text-sm border-collapse">
                      <thead>
                        <tr className="border-b border-border text-text-secondary" style={{ fontSize: '10px' }}>
                          <th className="text-left py-2 pr-4 uppercase tracking-widest">Action</th>
                          <th className="text-right py-2 uppercase tracking-widest">Count</th>
                        </tr>
                      </thead>
                      <tbody>
                        {Object.entries(operator?.by_action ?? {}).map(([action, n]) => (
                          <tr key={action} className="border-b border-border hover:bg-surface transition-colors">
                            <td className="py-2 pr-4 text-text-primary">{action}</td>
                            <td className="py-2 text-right text-text-secondary">{n}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
            </>
          )}
      </Section>

      <Section title="Learning">
        {gapOf(learning)
          ? <Gap text={gapOf(learning) as string} />
          : learning?.captured === false
            ? <Gap text={learning.reason ?? 'No learning snapshot was captured in this period.'} />
            : (
              <>
                <div className="flex gap-6 flex-wrap mb-4">
                  <Tile
                    value={(learning?.verdict ?? '—').toUpperCase()}
                    label="verdict"
                    tone={VERDICT_CLS[learning?.verdict ?? ''] ?? 'text-text-secondary'}
                  />
                  <Tile value={fmtPct(learning?.agreement_rate)} label="agreement rate"
                        sub={typeof learning?.agreement_vs_previous_month === 'number'
                          ? `${signed(Math.round(learning.agreement_vs_previous_month * 100))} pts vs last month`
                          : null} />
                  <Tile value={learning?.mean_confidence ?? '—'} label="mean confidence"
                        sub={typeof learning?.confidence_delta === 'number'
                          ? `${signed(learning.confidence_delta)} delta`
                          : null} />
                  <Tile value={fmtNum(learning?.sample_count)} label="samples" />
                  <Tile value={`${learning?.auto_publish_reverted ?? 0} / ${learning?.auto_publish_count ?? 0}`}
                        label="auto-publishes reverted"
                        tone={(learning?.auto_publish_reverted ?? 0) > 0 ? 'text-red' : undefined} />
                </div>
                <div className="font-body text-text-secondary text-sm">
                  Snapshot captured {fmtStamp(learning?.captured_at)} SGT
                  {learning?.previous
                    ? ` · previous snapshot ${fmtStamp(learning.previous.captured_at)} SGT, agreement `
                      + `${fmtPct(learning.previous.agreement_rate)} (${learning.previous.verdict})`
                    : ' · no earlier snapshot to compare against'}
                </div>
              </>
            )}
      </Section>

      <Section title="Ingestion">
        {gapOf(ingestion)
          ? <Gap text={gapOf(ingestion) as string} />
          : (ingestion?.passes ?? 0) === 0
            ? <Gap text="No ingestion passes ran in this period." />
            : (
              <>
                <div className="flex gap-6 flex-wrap mb-4">
                  <Tile value={fmtNum(ingestion?.passes)} label="passes" sub={vs('passes')} />
                  <Tile value={fmtNum(ingestion?.total_queued)} label="cards queued"
                        sub={vs('total_queued')} />
                  <Tile value={fmtNum(ingestion?.degraded_passes)} label="degraded passes"
                        tone={(ingestion?.degraded_passes ?? 0) > 0 ? 'text-yellow' : undefined}
                        sub={fmtPct(ingestion?.degraded_rate)} />
                  <Tile value={ingestion?.sources_blocked?.length ?? 0} label="sources blocked"
                        tone={(ingestion?.sources_blocked?.length ?? 0) > 0 ? 'text-red' : undefined} />
                </div>

                <div className="overflow-x-auto">
                  <table className="w-full font-body text-sm border-collapse">
                    <thead>
                      <tr className="border-b border-border text-text-secondary" style={{ fontSize: '10px' }}>
                        <th className="text-left py-2 pr-4 uppercase tracking-widest">Source</th>
                        <th className="text-right py-2 pr-4 uppercase tracking-widest">Passes</th>
                        <th className="text-right py-2 pr-4 uppercase tracking-widest">Fetched</th>
                        <th className="text-right py-2 pr-4 uppercase tracking-widest">Fresh</th>
                        <th className="text-right py-2 pr-4 uppercase tracking-widest">Queued</th>
                        <th className="text-right py-2 pr-4 uppercase tracking-widest">Blocked</th>
                        <th className="text-left py-2 uppercase tracking-widest">Last reason</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(ingestion?.per_source ?? []).map(s => (
                        <tr key={s.source} className="border-b border-border hover:bg-surface transition-colors">
                          <td className="py-2 pr-4 text-text-primary">{s.source}</td>
                          <td className="py-2 pr-4 text-right text-text-secondary">{s.passes}</td>
                          <td className="py-2 pr-4 text-right text-text-secondary">{s.fetched}</td>
                          <td className="py-2 pr-4 text-right text-text-secondary">{s.fresh}</td>
                          <td className="py-2 pr-4 text-right text-text-primary">{s.queued}</td>
                          <td className="py-2 pr-4 text-right">
                            <span className={s.blocked + s.unavailable > 0 ? 'text-red' : 'text-text-secondary'}>
                              {s.blocked + s.unavailable}
                            </span>
                          </td>
                          <td className="py-2 text-text-secondary" style={{ fontSize: '11px' }}>
                            {s.last_reason ?? '—'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
      </Section>

      <Section title="Agent reliability">
        {gapOf(reliability)
          ? <Gap text={gapOf(reliability) as string} />
          : (
            <>
              <div className="flex gap-6 flex-wrap mb-4">
                <Tile value={fmtNum(reliability?.runs)} label="runs" />
                <Tile value={fmtNum(reliability?.ok)} label="ok" tone="text-green" />
                <Tile value={fmtNum(reliability?.degraded)} label="degraded" tone="text-yellow" />
                <Tile value={fmtNum(reliability?.failed)} label="failed"
                      tone={(reliability?.failed ?? 0) > 0 ? 'text-red' : undefined} />
                <Tile value={fmtNum(reliability?.running)} label="never finished"
                      tone={(reliability?.running ?? 0) > 0 ? 'text-red' : undefined} />
              </div>

              {reliability?.runs_readable === false && (
                <div className="font-body text-text-secondary text-sm mb-4">
                  agent_runs was unreadable — only the event counts below are trustworthy.
                </div>
              )}

              {(reliability?.by_agent ?? []).length === 0
                ? <Gap text="No agent runs recorded in this period." />
                : (
                  <div className="overflow-x-auto mb-6">
                    <table className="w-full font-body text-sm border-collapse">
                      <thead>
                        <tr className="border-b border-border text-text-secondary" style={{ fontSize: '10px' }}>
                          <th className="text-left py-2 pr-4 uppercase tracking-widest">Agent</th>
                          <th className="text-right py-2 pr-4 uppercase tracking-widest">Runs</th>
                          <th className="text-right py-2 pr-4 uppercase tracking-widest">Ok</th>
                          <th className="text-right py-2 pr-4 uppercase tracking-widest">Degraded</th>
                          <th className="text-right py-2 pr-4 uppercase tracking-widest">Failed</th>
                          <th className="text-right py-2 pr-4 uppercase tracking-widest">Unfinished</th>
                          <th className="text-right py-2 uppercase tracking-widest">Avg</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(reliability?.by_agent ?? []).map(a => (
                          <tr key={a.agent} className="border-b border-border hover:bg-surface transition-colors">
                            <td className="py-2 pr-4 text-text-primary">{a.agent}</td>
                            <td className="py-2 pr-4 text-right text-text-secondary">{a.runs}</td>
                            <td className="py-2 pr-4 text-right text-green">{a.ok}</td>
                            <td className="py-2 pr-4 text-right text-yellow">{a.degraded}</td>
                            <td className={`py-2 pr-4 text-right ${a.failed > 0 ? 'text-red' : 'text-text-secondary'}`}>
                              {a.failed}
                            </td>
                            <td className={`py-2 pr-4 text-right ${a.running > 0 ? 'text-red' : 'text-text-secondary'}`}>
                              {a.running}
                            </td>
                            <td className="py-2 text-right text-text-secondary">{fmtMs(a.avg_duration_ms)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}

              <div className="font-body text-text-secondary text-sm uppercase tracking-widest mb-2"
                   style={{ fontSize: '10px' }}>
                Top error / anomaly events
              </div>
              {(reliability?.top_events ?? []).length === 0
                ? <Gap text="No errors or anomalies recorded in this period." />
                : (
                  <div className="overflow-x-auto">
                    <table className="w-full font-body text-sm border-collapse">
                      <thead>
                        <tr className="border-b border-border text-text-secondary" style={{ fontSize: '10px' }}>
                          <th className="text-left py-2 pr-4 uppercase tracking-widest">Event</th>
                          <th className="text-left py-2 pr-4 uppercase tracking-widest">Level</th>
                          <th className="text-right py-2 uppercase tracking-widest">Count</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(reliability?.top_events ?? []).map(e => (
                          <tr key={`${e.event}-${e.level}`} className="border-b border-border hover:bg-surface transition-colors">
                            <td className="py-2 pr-4 text-text-primary">{e.event}</td>
                            <td className={`py-2 pr-4 ${e.level === 'error' ? 'text-red' : 'text-yellow'}`}>
                              {e.level}
                            </td>
                            <td className="py-2 text-right text-text-secondary">{e.count}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
            </>
          )}
      </Section>

      <Section title="Backend health & cost">
        {gapOf(health)
          ? <Gap text={gapOf(health) as string} />
          : (health?.checks ?? 0) === 0
            ? <Gap text="No health checks recorded in this period." />
            : (
              <>
                <div className="flex gap-6 flex-wrap mb-4">
                  <Tile value={(health?.worst_status ?? '—').toUpperCase()} label="worst status"
                        tone={STATUS_CLS[health?.worst_status ?? ''] ?? 'text-text-secondary'} />
                  <Tile value={fmtNum(health?.checks)} label="checks" />
                  {health?.cost_guard && (
                    <Tile value={(health.cost_guard.status ?? '—').toUpperCase()} label="cost guard"
                          tone={STATUS_CLS[health.cost_guard.status ?? ''] ?? 'text-text-secondary'}
                          sub={health.cost_guard.message ?? null} />
                  )}
                </div>

                <div className="overflow-x-auto">
                  <table className="w-full font-body text-sm border-collapse">
                    <thead>
                      <tr className="border-b border-border text-text-secondary" style={{ fontSize: '10px' }}>
                        <th className="text-left py-2 pr-4 uppercase tracking-widest">Component</th>
                        <th className="text-left py-2 pr-4 uppercase tracking-widest">Worst</th>
                        <th className="text-left py-2 pr-4 uppercase tracking-widest">Last</th>
                        <th className="text-right py-2 pr-4 uppercase tracking-widest">Checks</th>
                        <th className="text-left py-2 pr-4 uppercase tracking-widest">Last check (SGT)</th>
                        <th className="text-left py-2 uppercase tracking-widest">Message</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(health?.components ?? []).map(c => (
                        <tr key={c.component} className="border-b border-border hover:bg-surface transition-colors">
                          <td className="py-2 pr-4 text-text-primary">{c.component}</td>
                          <td className={`py-2 pr-4 ${STATUS_CLS[c.worst_status] ?? 'text-text-secondary'}`}>
                            {c.worst_status.toUpperCase()}
                          </td>
                          <td className={`py-2 pr-4 ${STATUS_CLS[c.last_status ?? ''] ?? 'text-text-secondary'}`}>
                            {(c.last_status ?? '—').toUpperCase()}
                          </td>
                          <td className="py-2 pr-4 text-right text-text-secondary">{c.checks}</td>
                          <td className="py-2 pr-4 text-text-secondary">{fmtStamp(c.last_checked_at)}</td>
                          <td className="py-2 text-text-secondary" style={{ fontSize: '11px' }}>
                            {c.message ?? '—'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
      </Section>

      <Section title="Notifications">
        {gapOf(notifications)
          ? <Gap text={gapOf(notifications) as string} />
          : (notifications?.total ?? 0) === 0
            ? <Gap text="No notifications were logged in this period." />
            : (
              <div className="overflow-x-auto">
                <table className="w-full font-body text-sm border-collapse">
                  <thead>
                    <tr className="border-b border-border text-text-secondary" style={{ fontSize: '10px' }}>
                      <th className="text-left py-2 pr-4 uppercase tracking-widest">Kind</th>
                      <th className="text-right py-2 pr-4 uppercase tracking-widest">Sent</th>
                      <th className="text-right py-2 pr-4 uppercase tracking-widest">Suppressed</th>
                      <th className="text-right py-2 pr-4 uppercase tracking-widest">Failed</th>
                      <th className="text-right py-2 pr-4 uppercase tracking-widest">Disabled</th>
                      <th className="text-right py-2 uppercase tracking-widest">Total</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(notifications?.by_kind ?? []).map(k => (
                      <tr key={k.kind} className="border-b border-border hover:bg-surface transition-colors">
                        <td className="py-2 pr-4 text-text-primary">{k.kind}</td>
                        <td className="py-2 pr-4 text-right text-green">{k.sent}</td>
                        <td className="py-2 pr-4 text-right text-text-secondary">{k.suppressed}</td>
                        <td className={`py-2 pr-4 text-right ${k.failed > 0 ? 'text-red' : 'text-text-secondary'}`}>
                          {k.failed}
                        </td>
                        <td className="py-2 pr-4 text-right text-text-secondary">{k.disabled}</td>
                        <td className="py-2 text-right text-text-primary">{k.total}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
      </Section>

      <Section title="Previous period">
        <div className="font-body text-text-secondary text-sm">
          {prev.start && prev.end
            ? <>Compared against {fmtDay(prev.start)} – {fmtDay(prev.end)}: {fmtNum(prev.published)} published,{' '}
               {fmtNum(prev.auto_published)} auto-published, {fmtNum(prev.operator_decisions)} operator decisions,{' '}
               {fmtNum(prev.total_queued)} queued over {fmtNum(prev.passes)} passes.</>
            : 'No previous period to compare against.'}
        </div>
      </Section>

      {(body.warnings ?? []).length > 0 && (
        <Section title="Data gaps">
          <ul className="font-body text-yellow text-sm space-y-1">
            {(body.warnings ?? []).map((w, i) => <li key={i}>! {w}</li>)}
          </ul>
        </Section>
      )}
    </div>
  )
}
