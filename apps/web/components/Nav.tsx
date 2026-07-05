'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'

const LINKS = [
  { href: '/',         label: 'MAP'      },
  { href: '/timeline', label: 'TIMELINE' },
  { href: '/about',    label: 'ABOUT'    },
]

export function Nav() {
  const path = usePathname()

  return (
    <header
      className="flex-none flex items-center justify-between px-4 relative z-[100]"
      style={{ height: 72, background: 'var(--color-bg)', borderBottom: '1px solid var(--color-border)' }}
    >
      {/* Logo — two-line stack with breathing room above and below.
          On the homepage the logo doubles as the page <h1> (the HUD has no
          other heading); Tailwind preflight resets h1 to inherit font/margin,
          so it renders pixel-identical. Other pages have their own <h1>. */}
      <Link
        href="/"
        className="font-display block"
        style={{ fontSize: 26, color: 'var(--color-amber)', lineHeight: '1.05' }}
      >
        {path === '/' ? (
          <h1>
            YISHUN<br />AGAIN
            <span className="sr-only"> — satirical incident map of Yishun, Nee Soon, Singapore</span>
          </h1>
        ) : (
          <>YISHUN<br />AGAIN</>
        )}
      </Link>

      {/* Nav links — right-aligned, consistent spacing */}
      <nav className="flex items-center gap-7">
        {LINKS.map(({ href, label }) => {
          const active = path === href
          return (
            <Link
              key={href}
              href={href}
              className="font-display leading-none whitespace-nowrap"
              style={{ fontSize: 14, color: active ? 'var(--color-sienna)' : 'var(--color-amber)' }}
            >
              {label}
            </Link>
          )
        })}
      </nav>
    </header>
  )
}
