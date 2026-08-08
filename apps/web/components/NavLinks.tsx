'use client'

import Link from 'next/link'
import { usePathname, useSearchParams } from 'next/navigation'
import { buildHref } from '@/lib/params'

const LINKS: Array<{ href: string; label: string }> = [
  { href: '/',         label: 'FEED'     },
  { href: '/map',      label: 'MAP'      },
  { href: '/timeline', label: 'TIMELINE' },
  { href: '/about',    label: 'ABOUT'    },
]

function linkStyle(active: boolean) {
  return { fontSize: 14, color: active ? 'var(--color-sienna)' : 'var(--color-amber)' }
}

// Shared markup. `hrefFor` decides whether the query string is carried across
// (real links) or dropped (the static fallback, which has no client params yet).
function View({ path, hrefFor }: { path: string; hrefFor: (base: string) => string }) {
  return (
    <nav className="flex items-center gap-7">
      {LINKS.map(({ href, label }) => (
        <Link
          key={href}
          href={hrefFor(href)}
          className="font-display leading-none whitespace-nowrap"
          style={linkStyle(path === href)}
        >
          {label}
        </Link>
      ))}
    </nav>
  )
}

/**
 * Nav links that carry `?year=` (and later `?class=`) across the /↔/map↔… hops,
 * so the selected year survives navigation. useSearchParams keeps this in its
 * own component behind a Suspense boundary — the parent Nav renders the <h1>
 * with usePathname alone and stays in the static HTML.
 */
export function NavLinks() {
  const path         = usePathname()
  const searchParams = useSearchParams()
  return <View path={path} hrefFor={base => buildHref(base, searchParams)} />
}

/** Static/pre-hydration fallback — same links, no query string carried. */
export function NavLinksFallback({ path }: { path: string }) {
  return <View path={path} hrefFor={base => base} />
}
