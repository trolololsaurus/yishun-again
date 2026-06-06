import type { Metadata } from 'next'
import './globals.css'
import { Nav } from '@/components/Nav'

export const metadata: Metadata = {
  title:  'War Room — Yishun Again',
  description: 'Private operator CMS — Yishun Again incident archive.',
  robots: 'noindex, nofollow',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-bg flex">
        <Nav />
        <main className="flex-1 ml-48 p-8 min-h-screen">
          {children}
        </main>
      </body>
    </html>
  )
}
