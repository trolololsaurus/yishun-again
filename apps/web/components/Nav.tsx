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
      {/* Logo — two-line stack with breathing room above and below */}
      <Link
        href="/"
        className="font-display block"
        style={{ fontSize: 26, color: 'var(--color-amber)', lineHeight: '1.05' }}
      >
        YISHUN<br />AGAIN
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
