import { NextResponse } from 'next/server'

const VALID_WINDOWS = new Set(['24h', '7d'])

export async function GET(req: Request) {
  const agentsUrl = (process.env.AGENTS_API_URL ?? '').replace(/\/+$/, '')
  const opsToken  = process.env.OPS_TOKEN
  if (!agentsUrl || !opsToken) {
    return NextResponse.json({ error: 'AGENTS_API_URL / OPS_TOKEN not configured' }, { status: 503 })
  }

  const requested = new URL(req.url).searchParams.get('window')
  const window    = VALID_WINDOWS.has(requested ?? '') ? requested : '7d'

  try {
    const res = await fetch(`${agentsUrl}/analytics/cloudflare?window=${window}`, {
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
