import { supabase } from '@/lib/supabase'
import { QueueList } from '@/components/QueueList'
import { BackfillBanner } from '@/components/BackfillBanner'
import { RecentMerges, type MergedRow } from '@/components/RecentMerges'
import { isoDaysAgo, isDiscoverySource, primaryIdOf } from '@/lib/utils'
import type { QueueItem, IncidentPreview, AgentRelatedIncident } from '@/lib/types'

export const revalidate = 0

export default async function QueuePage() {
  const since24h = isoDaysAgo(1)
  const since7d  = isoDaysAgo(7)

  const [queueResult, healthResult, queuedCountResult, backfillSummaryResult, recentMergesResult] = await Promise.all([
    supabase
      .from('war_room_queue')
      .select('*')
      .in('status', ['pending', 'update'])
      .order('created_at', { ascending: false }),
    supabase
      .from('scraper_health')
      // status_reason is why the row is amber/red ("0 items for 30 consecutive
      // runs", "HTTP 403"). It was written by ingestion/health.py from the very
      // start but never selected, so the panel showed "WARNING" with no way to
      // tell a dead scraper from a quiet one without opening the database.
      .select('source_name, status, status_reason, items_found, items_passed_s1, scraped_at')
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
    // Applied updates from the last 7 days, for the Undo panel.
    supabase
      .from('war_room_queue')
      .select('id, incident_id, source_url, proposed_title, processed_at, agent_confidence, raw_content')
      .eq('status', 'update_approved')
      .gte('processed_at', since7d)
      .order('processed_at', { ascending: false })
      .limit(50),
  ])

  if (queueResult.error) {
    return (
      <div className="text-red text-sm">
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
      .select('id,title,summary,slug,classification,severity,incident_date,source_urls,update_count,is_developing,edmw_signal_count')
      .in('id', targetIds)

    for (const t of targets ?? []) {
      targetIncidents[t.id] = {
        id:                t.id,
        title:             t.title,
        summary:           t.summary,
        slug:              t.slug,
        classification:    t.classification,
        severity:          t.severity,
        incident_date:     t.incident_date,
        source_urls:       t.source_urls ?? [],
        update_count:      t.update_count ?? 0,
        is_developing:     t.is_developing ?? false,
        edmw_signal_count: t.edmw_signal_count ?? 0,
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
  // Carry the reason through, not just the name — an alert you cannot act on
  // is noise, and "WARNING" alone reads identically whether a source is broken
  // or simply had no Yishun news this month.
  //
  // A discovery adapter (sitemap/search) erroring while its outlet's PRIMARY
  // scraper is healthy is NOT an outlet outage — it reads as "Straits Times is
  // down" when ST is publishing fine. Demote those to an informational line so
  // the red ERROR tier means what it says: the outlet's own feed failed.
  const okIds = new Set(latestPerSource.filter(r => r.status === 'ok').map(r => r.source_name))
  const isDemoted = (r: { source_name: string }) =>
    isDiscoverySource(r.source_name) && okIds.has(primaryIdOf(r.source_name))
  const allErrors = latestPerSource.filter(r => r.status === 'error')
  const errorSources = allErrors.filter(r => !isDemoted(r))
  const discoveryDegraded = allErrors.filter(isDemoted)
  const warningSources = latestPerSource.filter(r => r.status === 'warning')
  const hasAlerts      = errorSources.length > 0 || warningSources.length > 0
                         || discoveryDegraded.length > 0

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

  // ── Recently-merged updates (Undo panel) ───────────────────────────────────
  const mergeRows = recentMergesResult.data ?? []
  const mergeIncidentIds = [...new Set(mergeRows.map(m => m.incident_id).filter(Boolean) as string[])]
  const mergeIncidents: Record<string, { title: string; slug: string }> = {}
  if (mergeIncidentIds.length > 0) {
    const { data: mi } = await supabase
      .from('incidents')
      .select('id,title,slug')
      .in('id', mergeIncidentIds)
    for (const r of mi ?? []) mergeIncidents[r.id] = { title: r.title, slug: r.slug }
  }
  const recentMerges: MergedRow[] = mergeRows.map(m => {
    const rc = (m.raw_content ?? {}) as Record<string, unknown>
    const inc = m.incident_id ? mergeIncidents[m.incident_id] : undefined
    const mc = rc._match_confidence
    return {
      id:              m.id,
      incidentTitle:   inc?.title ?? '(incident not found)',
      incidentSlug:    inc?.slug ?? null,
      sourceUrl:       m.source_url,
      headline:        m.proposed_title ?? '',
      processedAt:     m.processed_at,
      hasSnapshot:     !!rc._undo_snapshot,
      matchConfidence: typeof mc === 'number' ? mc : null,
      draftConfidence: m.agent_confidence ?? null,
    }
  })

  // Filter summary sentinel rows out of the main queue list
  const displayItems = queueItems.filter(i => {
    const rc = i.raw_content as Record<string, unknown>
    return rc?.notification_type !== 'backfill_summary'
  })

  return (
    <div>
      <h1 className="font-bold text-yellow text-lg mb-6">QUEUE</h1>

      {hasHealthData && (
        <div className="mb-6 border border-border bg-surface p-4">
          <div className="text-text-secondary mb-3 uppercase tracking-widest text-sm">
            Last 24h
          </div>

          {hasAlerts && (
            <div className="mb-3 space-y-1.5 text-sm">
              {errorSources.map(r => (
                <div key={r.source_name} className="text-red">
                  🔴 {r.source_name} — ERROR
                  {r.status_reason && (
                    <span className="text-text-secondary"> · {r.status_reason}</span>
                  )}
                </div>
              ))}
              {warningSources.map(r => (
                <div key={r.source_name} className="text-yellow">
                  🟡 {r.source_name} — WARNING
                  {r.status_reason ? (
                    <span className="text-text-secondary"> · {r.status_reason}</span>
                  ) : (
                    <span className="text-text-secondary"> · no reason recorded</span>
                  )}
                </div>
              ))}
              {/* Discovery adapter failed but the outlet's primary feed is
                  healthy — degraded archive depth, not an outage. */}
              {discoveryDegraded.map(r => (
                <div key={r.source_name} className="text-text-secondary">
                  🔵 {r.source_name} — discovery only
                  {r.status_reason && <span> · {r.status_reason}</span>}
                  <span> · {primaryIdOf(r.source_name)} primary feed OK, outlet still covered</span>
                </div>
              ))}
              {/* A zero-item run is the NORMAL case: items_found counts
                  candidates that survived the Yishun keyword filter, not
                  articles served, so one outlet can go a month without a
                  Yishun story. Say so, rather than leaving the operator to
                  read a quiet source as a broken one. */}
              <div className="text-text-secondary pt-1 text-xs">
                A long zero streak usually means no Yishun news from that outlet,
                not a broken scraper. Real failures show as ERROR.
              </div>
            </div>
          )}

          <div className="flex gap-10">
            <div>
              <div className="text-text-secondary mb-1 text-sm">SCRAPED</div>
              <div className="text-text-primary font-bold text-lg">{scraped24h}</div>
            </div>
            <div>
              <div className="text-text-secondary mb-1 text-sm">PASSED S1</div>
              <div className="text-text-primary font-bold text-lg">
                {passedS1_24h > 0 ? passedS1_24h : '—'}
              </div>
            </div>
            <div>
              <div className="text-text-secondary mb-1 text-sm">QUEUED</div>
              <div className="text-text-primary font-bold text-lg">{queued24h}</div>
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

      <RecentMerges initial={recentMerges} siteUrl={process.env.NEXT_PUBLIC_SITE_URL ?? ''} />

      <QueueList
        initialItems={displayItems}
        targetIncidents={targetIncidents}
        relatedPreviews={relatedPreviews}
        siteUrl={process.env.NEXT_PUBLIC_SITE_URL ?? ''}
      />
    </div>
  )
}
