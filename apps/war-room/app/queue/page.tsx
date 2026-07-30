import { supabase } from '@/lib/supabase'
import { QueueList } from '@/components/QueueList'
import { BackfillBanner } from '@/components/BackfillBanner'
import type { QueueItem, IncidentPreview, AgentRelatedIncident } from '@/lib/types'

export const revalidate = 0

export default async function QueuePage() {
  const since24h = new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString()

  const [queueResult, healthResult, queuedCountResult, backfillSummaryResult] = await Promise.all([
    supabase
      .from('war_room_queue')
      .select('*')
      .in('status', ['pending', 'update'])
      .order('created_at', { ascending: false }),
    supabase
      .from('scraper_health')
      .select('source_name, status, items_found, items_passed_s1, scraped_at')
      .gte('scraped_at', since24h)
      .order('scraped_at', { ascending: false })
      .limit(500),
    supabase
      .from('war_room_queue')
      .select('id', { count: 'exact', head: true })
      .gte('created_at', since24h),
    // Most recent backfill summary notification (notification_type sentinel row).
    // \_ escapes LIKE's single-char wildcard — unescaped, any 18-char prefix
    // ending in "backfill?summary?" would also match the sentinel filter.
    supabase
      .from('war_room_queue')
      .select('raw_content, created_at')
      .like('source_url', '\\_backfill\\_summary\\_%')
      .order('created_at', { ascending: false })
      .limit(1),
  ])

  if (queueResult.error) {
    return (
      <div className="font-body text-red text-sm">
        Failed to load queue: {queueResult.error.message}
      </div>
    )
  }

  const queueItems = (queueResult.data ?? []) as QueueItem[]

  // ── Batch-fetch target incidents for UPDATE items ──────────────────────────
  const targetIds = [
    ...new Set(
      queueItems
        .filter(i => i.status === 'update' && i.update_target_incident_id)
        .map(i => i.update_target_incident_id as string)
    ),
  ]

  const targetIncidents: Record<string, IncidentPreview> = {}
  if (targetIds.length > 0) {
    const { data: targets } = await supabase
      .from('incidents')
      .select('id,title,summary,slug,classification,severity,incident_date,source_urls,update_count,is_developing')
      .in('id', targetIds)

    for (const t of targets ?? []) {
      targetIncidents[t.id] = {
        id:             t.id,
        title:          t.title,
        summary:        t.summary,
        slug:           t.slug,
        classification: t.classification,
        severity:       t.severity,
        incident_date:  t.incident_date,
        source_urls:    t.source_urls ?? [],
        update_count:   t.update_count ?? 0,
        is_developing:  t.is_developing ?? false,
      }
    }
  }

  // ── Batch-fetch related incident previews ──────────────────────────────────
  // Collect IDs from: agent_related_incidents banners AND pattern_alert incident lists
  const relatedIds = [
    ...new Set(
      queueItems.flatMap(i => {
        const rc = i.raw_content as Record<string, unknown>
        const fromRelated = ((rc?.agent_related_incidents as AgentRelatedIncident[]) ?? []).map(r => r.incident_id)
        const fromPattern = rc?.notification_type === 'pattern_alert'
          ? ((rc?.incident_ids as string[]) ?? [])
          : []
        return [...fromRelated, ...fromPattern]
      })
    ),
  ]

  const relatedPreviews: Record<string, { title: string; slug: string }> = {}
  if (relatedIds.length > 0) {
    const { data: related } = await supabase
      .from('incidents')
      .select('id,title,slug')
      .in('id', relatedIds)

    for (const r of related ?? []) {
      relatedPreviews[r.id] = { title: r.title, slug: r.slug }
    }
  }

  // ── Health panel data ──────────────────────────────────────────────────────
  const healthRows     = healthResult.data ?? []
  const hasHealthData  = healthRows.length > 0
  const scraped24h     = healthRows.reduce((s, r) => s + (r.items_found    ?? 0), 0)
  const passedS1_24h   = healthRows.reduce((s, r) => s + (r.items_passed_s1 ?? 0), 0)
  const queued24h      = queuedCountResult.count ?? 0

  const seen = new Set<string>()
  const latestPerSource: typeof healthRows = []
  for (const r of healthRows) {
    if (!seen.has(r.source_name)) {
      seen.add(r.source_name)
      latestPerSource.push(r)
    }
  }
  const errorSources   = latestPerSource.filter(r => r.status === 'error').map(r => r.source_name)
  const warningSources = latestPerSource.filter(r => r.status === 'warning').map(r => r.source_name)
  const hasAlerts      = errorSources.length > 0 || warningSources.length > 0

  // ── Backfill banner data ───────────────────────────────────────────────────
  const backfillRow = (backfillSummaryResult.data ?? [])[0]
  const backfillStats = backfillRow
    ? (backfillRow.raw_content as Record<string, unknown>)
    : null

  // Count pending backfill items in the current queue (for bulk action totals)
  const backfillPendingItems = queueItems.filter(i => {
    const rc = i.raw_content as Record<string, unknown>
    return rc?._backfill === true && i.status === 'pending'
  })
  const backfillHighConf  = backfillPendingItems.filter(i => (i.agent_confidence ?? 0) >= 0.85)
  const backfillLowConf   = backfillPendingItems.filter(i => (i.agent_confidence ?? 0) < 0.6)

  // Filter summary sentinel rows out of the main queue list
  const displayItems = queueItems.filter(i => {
    const rc = i.raw_content as Record<string, unknown>
    return rc?.notification_type !== 'backfill_summary'
  })

  return (
    <div>
      <h1 className="font-body font-bold text-yellow text-lg mb-6">QUEUE</h1>

      {hasHealthData && (
        <div className="mb-6 border border-border bg-surface p-4">
          <div className="font-body text-text-secondary mb-3 uppercase tracking-widest" style={{ fontSize: '10px' }}>
            Last 24h
          </div>

          {hasAlerts && (
            <div className="mb-3 font-body space-y-1" style={{ fontSize: '11px' }}>
              {errorSources.map(src => (
                <div key={src} className="text-red">🔴 {src} — ERROR</div>
              ))}
              {warningSources.map(src => (
                <div key={src} className="text-yellow">🟡 {src} — WARNING</div>
              ))}
            </div>
          )}

          <div className="flex gap-10 font-body">
            <div>
              <div className="text-text-secondary mb-1" style={{ fontSize: '10px' }}>SCRAPED</div>
              <div className="text-text-primary font-bold text-base">{scraped24h}</div>
            </div>
            <div>
              <div className="text-text-secondary mb-1" style={{ fontSize: '10px' }}>PASSED S1</div>
              <div className="text-text-primary font-bold text-base">
                {passedS1_24h > 0 ? passedS1_24h : '—'}
              </div>
            </div>
            <div>
              <div className="text-text-secondary mb-1" style={{ fontSize: '10px' }}>QUEUED</div>
              <div className="text-text-primary font-bold text-base">{queued24h}</div>
            </div>
          </div>
        </div>
      )}

      {backfillStats && (
        <BackfillBanner
          stats={backfillStats}
          runAt={backfillRow?.created_at as string}
          highConfCount={backfillHighConf.length}
          lowConfCount={backfillLowConf.length}
          highConfIds={backfillHighConf.map(i => i.id)}
          lowConfIds={backfillLowConf.map(i => i.id)}
        />
      )}

      <QueueList
        initialItems={displayItems}
        targetIncidents={targetIncidents}
        relatedPreviews={relatedPreviews}
        siteUrl={process.env.NEXT_PUBLIC_SITE_URL ?? ''}
      />
    </div>
  )
}
