'use client'

import { useState } from 'react'
import { QueueCard }        from './QueueCard'
import { UpdateCard }       from './UpdateCard'
import { LifecycleCard }    from './LifecycleCard'
import { PatternAlertCard } from './PatternAlertCard'
import type { QueueItem, IncidentPreview } from '@/lib/types'

interface Props {
  initialItems:    QueueItem[]
  targetIncidents: Record<string, IncidentPreview>
  relatedPreviews: Record<string, { title: string; slug: string }>
  siteUrl:         string
}

export function QueueList({ initialItems, targetIncidents, relatedPreviews, siteUrl }: Props) {
  const [items, setItems] = useState<QueueItem[]>(initialItems)

  function handleProcessed(id: string) {
    setItems(prev => prev.filter(item => item.id !== id))
  }

  if (items.length === 0) {
    return (
      <div className="text-center py-24 font-body text-text-secondary">
        <div className="text-2xl mb-4">✓</div>
        <div>Queue empty. Nothing pending review.</div>
      </div>
    )
  }

  const pendingCount   = items.filter(i => i.status === 'pending' && !notifType(i)).length
  const updateCount    = items.filter(i => i.status === 'update').length
  const lifecycleCount = items.filter(i => notifType(i) === 'lifecycle_concluded').length
  const patternCount   = items.filter(i => notifType(i) === 'pattern_alert').length

  return (
    <div className="space-y-6">
      {/* Counts bar */}
      <div className="font-body text-text-secondary text-sm mb-4 flex flex-wrap gap-x-3 gap-y-1">
        {pendingCount > 0   && <span>{pendingCount} pending</span>}
        {updateCount > 0    && <span className="text-cyan-400">{updateCount} update{updateCount !== 1 ? 's' : ''}</span>}
        {lifecycleCount > 0 && <span className="text-text-secondary">{lifecycleCount} lifecycle</span>}
        {patternCount > 0   && <span className="text-orange-400">{patternCount} pattern alert{patternCount !== 1 ? 's' : ''}</span>}
      </div>

      {items.map(item => {
        const nt = notifType(item)

        if (nt === 'lifecycle_concluded') {
          return (
            <LifecycleCard key={item.id} item={item} onProcessed={handleProcessed} />
          )
        }

        if (nt === 'pattern_alert') {
          return (
            <PatternAlertCard
              key={item.id}
              item={item}
              relatedPreviews={relatedPreviews}
              siteUrl={siteUrl}
              onProcessed={handleProcessed}
            />
          )
        }

        if (item.status === 'update' && item.update_target_incident_id) {
          const target = targetIncidents[item.update_target_incident_id]
          if (!target) return null
          return (
            <UpdateCard
              key={item.id}
              item={item}
              targetIncident={target}
              relatedPreviews={relatedPreviews}
              onProcessed={handleProcessed}
            />
          )
        }

        return (
          <QueueCard
            key={item.id}
            item={item}
            relatedPreviews={relatedPreviews}
            onProcessed={handleProcessed}
          />
        )
      })}
    </div>
  )
}

function notifType(item: QueueItem): string | undefined {
  return (item.raw_content as Record<string, unknown>)?.notification_type as string | undefined
}
