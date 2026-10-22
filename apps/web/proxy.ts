import { NextRequest, NextResponse } from 'next/server'

const CANONICAL_HOST = 'www.yishunagain.com'

export function proxy(request: NextRequest) {
  const url = new URL(request.url)
  const path = url.pathname

  if (path === '/$' || path === '/&') return new NextResponse(null, { status: 410 })

  if (path === '/incidents/pma-rider-crash-yishun-pedestrian-path') {
    url.pathname = '/incidents/pma-rider-overturn-yishun-park-connector'
    return NextResponse.redirect(url, 301)
  }

  if (url.host.toLowerCase() !== CANONICAL_HOST || url.protocol !== 'https:' || (path !== '/' && path.endsWith('/'))) {
    url.protocol = 'https:'
    url.hostname = CANONICAL_HOST
    url.port = ''
    if (path !== '/') url.pathname = path.replace(/\/+$/, '')
    return NextResponse.redirect(url, 301)
  }

  return NextResponse.next()
}

export const config = { matcher: '/:path*' }
