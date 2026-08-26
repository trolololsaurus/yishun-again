import { NextResponse } from 'next/server'

export async function GET() {
  const agentsUrl = process.env.AGENTS_INTERNAL_URL
  const opsToken  = process.env.OPS_TOKEN
  if (!agentsUrl || !opsToken) {
    return NextResponse.json({ error: 'AGENTS_INTERNAL_URL / OPS_TOKEN not configured' }, { status: 503 })
  }

  try {
    const res = await fetch(`${agentsUrl}/analytics/cloudflare?days=7`, {
      headers: { 'X-Ops-Token': opsToken },
      cache:   'no-store',
    })
    if (!res.ok) {
      return NextResponse.json({ error: `Agents backend returned ${res.status}` }, { status: 502 })
    }
    const data = await res.json()
    return NextResponse.json(data)
  } catch {
    return NextResponse.json({ error: 'Failed to reach agents backend' }, { status: 503 })
  }
}
