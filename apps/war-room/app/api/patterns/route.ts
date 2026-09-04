import { NextResponse } from 'next/server'
import { supabase } from '@/lib/supabase'

// List every pattern (published or not) with each linked incident resolved to
// {id, slug, title, incident_date} for display, plus which ids were auto-added
// (so the operator can review machine appends) and which are excluded.

export async function GET() {
  const { data: patterns, error } = await supabase
    .from('patterns')
    .select('id,slug,title,thesis,published,incident_ids,auto_added_incident_ids,excluded_incident_ids,updated_at')
    .order('created_at', { ascending: false })

  if (error) {
    console.error('GET /api/patterns:', error)
    return NextResponse.json({ error: 'Fetch failed' }, { status: 500 })
  }

  const allIds = [...new Set((patterns ?? []).flatMap(p => p.incident_ids ?? []))]
  const byId: Record<string, { slug: string; title: string; incident_date: string | null }> = {}
  if (allIds.length > 0) {
    const { data: incs } = await supabase
      .from('incidents')
      .select('id,slug,title,incident_date')
      .in('id', allIds)
    for (const i of incs ?? []) byId[i.id] = { slug: i.slug, title: i.title, incident_date: i.incident_date }
  }

  return NextResponse.json({ patterns: patterns ?? [], incidents: byId })
}
