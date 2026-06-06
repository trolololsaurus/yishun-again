import { NextResponse }   from 'next/server'
import { revalidatePath } from 'next/cache'

export const revalidate = 0

export async function POST(req: Request) {
  const secret = process.env.REVALIDATE_SECRET
  if (!secret) {
    return NextResponse.json({ error: 'REVALIDATE_SECRET not configured' }, { status: 500 })
  }

  const auth = req.headers.get('authorization') ?? ''
  if (auth !== `Bearer ${secret}`) {
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
