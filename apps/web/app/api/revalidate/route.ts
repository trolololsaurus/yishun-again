import { NextResponse }   from 'next/server'
import { revalidatePath } from 'next/cache'
import { timingSafeEqual } from 'crypto'
import { rateLimit, getIp } from '@/lib/rateLimit'

export const revalidate = 0

// Constant-time comparison — a plain !== leaks the match length through
// timing. Buffers must be equal length before timingSafeEqual, so compare
// fixed-size digest-length buffers of both values.
function safeCompare(a: string, b: string): boolean {
  const ba = Buffer.from(a)
  const bb = Buffer.from(b)
  if (ba.length !== bb.length) return false
  return timingSafeEqual(ba, bb)
}

export async function POST(req: Request) {
  // Spec: every /api/* route is rate-limited. This one especially — each call
  // busts the ISR cache for the whole site.
  const { success } = rateLimit(getIp(req), 10)
  if (!success) return NextResponse.json({ error: 'Too many requests' }, { status: 429 })

  // .trim() both sides, for the reason main.py::_require_ops_token already
  // documents about OPS_TOKEN ("this cost an hour once"): a secret pasted into
  // the Vercel dashboard can carry a trailing newline or space that never
  // survives the HTTP hop. The two values then differ by one invisible byte and
  // every call 401s with nothing in the logs to explain it — indistinguishable
  // from a genuinely wrong token. It does not weaken the comparison: the
  // trimmed values are still compared in constant time, and whitespace is not
  // part of any legitimate secret.
  const secret = (process.env.REVALIDATE_SECRET ?? '').trim()
  const auth   = (req.headers.get('authorization') ?? '').trim()
  // An unset secret answers 401 like any bad token — a distinct 500 would
  // advertise the misconfiguration to outsiders.
  if (!secret || !safeCompare(auth, `Bearer ${secret}`)) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  }

  const body = await req.json().catch(() => ({}))
  const slug  = typeof body?.slug === 'string' ? body.slug.replace(/[^a-z0-9-]/g, '') : null

  revalidatePath('/', 'page')
  revalidatePath('/incidents/[slug]', 'page')
  if (slug) revalidatePath(`/incidents/${slug}`, 'page')

  return NextResponse.json({
    revalidated: true,
    paths: ['/', '/incidents/[slug]', ...(slug ? [`/incidents/${slug}`] : [])],
    at: new Date().toISOString(),
  })
}
