import { NextResponse } from 'next/server'

export async function GET() {
  const agentsUrl = process.env.AGENTS_INTERNAL_URL
  if (!agentsUrl) {
    return NextResponse.json({ error: 'AGENTS_INTERNAL_URL not configured' }, { status: 503 })
  }

  try {
    const res = await fetch(`${agentsUrl}/autonomy/status`, { cache: 'no-store' })
    if (!res.ok) {
      return NextResponse.json({ error: `Agents backend returned ${res.status}` }, { status: 502 })
    }
    const data = await res.json()
    return NextResponse.json(data)
  } catch {
    return NextResponse.json({ error: 'Failed to reach agents backend' }, { status: 503 })
  }
}
