'use client'

import Link from 'next/link'
import { Suspense } from 'react'
import { usePathname } from 'next/navigation'
import { NavLinks, NavLinksFallback } from './NavLinks'

export function Nav() {
  const path = usePathname()

  // The HUD roots (Feed and Map) have no visible page heading, so the logo
  // doubles as the page <h1>. Tailwind preflight resets h1 to inherit
  // font/margin, so it renders pixel-identical to the plain logo. Other pages
  // carry their own <h1>. usePathname is static-render-safe (unlike
  // useSearchParams), so this <h1> stays in the prerendered HTML.
  const isHudRoot = path === '/' || path === '/map'
  const hudHint   = path === '/map' ? 'incident map' : 'incident feed'

  return (
    <header
      className="flex-none flex items-center justify-between px-3 md:px-4 relative z-[100]"
      style={{ height: 72, background: 'var(--color-bg)', borderBottom: '1px solid var(--color-border)' }}
    >
      {/* Logo — two-line stack. Links home (brand reset); the query string is
          deliberately not carried here (the FEED nav link is the param-aware
          route back to the feed). */}
      <Link
        href="/"
        className="font-display block text-[18px] md:text-[26px]"
        style={{ color: 'var(--color-amber)', lineHeight: '1.05' }}
      >
        {isHudRoot ? (
          <h1>
            YISHUN<br />AGAIN
            <span className="sr-only"> — satirical {hudHint} of Yishun, Nee Soon, Singapore</span>
          </h1>
        ) : (
          <>YISHUN<br />AGAIN</>
        )}
      </Link>

      {/* Nav links — right-aligned. Param-aware, so ?year= survives navigation;
          a plain-link fallback covers the static / pre-hydration render. */}
      <Suspense fallback={<NavLinksFallback path={path} />}>
        <NavLinks />
      </Suspense>
    </header>
  )
}
