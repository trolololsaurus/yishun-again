import type { Metadata } from 'next'
import { Suspense } from 'react'
import './globals.css'
import 'maplibre-gl/dist/maplibre-gl.css'
import { Nav }             from '@/components/Nav'
import { UTMLogger }       from '@/components/UTMLogger'
import { PageViewTracker } from '@/components/PageViewTracker'
import { SITE_URL }        from '@/lib/site'

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title:       { default: 'Yishun Again', template: '%s · Yishun Again' },
  description: "Singapore's Most Cursed Estate — Documented.",
  robots:      'index, follow',
  openGraph: {
    siteName: 'Yishun Again',
    locale:   'en_SG',
    type:     'website',
  },
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en-SG" className="h-full">
      <body className="h-full bg-bg text-text-primary flex flex-col overflow-hidden">
        <Nav />
        <Suspense fallback={null}><UTMLogger /></Suspense>
        <PageViewTracker />
        <main className="flex-1 min-h-0 flex flex-col overflow-y-auto">{children}</main>
      </body>
    </html>
  )
}
