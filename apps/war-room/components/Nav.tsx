'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'

const LINKS = [
  { href: '/queue',     label: 'Queue' },
  { href: '/incidents', label: 'Incidents' },
  { href: '/sources',   label: 'Sources' },
  { href: '/analytics', label: 'Analytics' },
  { href: '/health',    label: 'Health' },
  { href: '/reports',   label: 'Reports' },
]

export function Nav() {
  const path = usePathname()

  return (
    <nav className="fixed top-0 left-0 h-full w-48 bg-surface border-r border-border flex flex-col z-50">
      <div className="p-4 border-b border-border">
        <span className="font-display text-red text-sm leading-relaxed block">
          WAR<br />ROOM
        </span>
        <span className="font-body text-text-secondary text-sm mt-1 block">
          Yishun Again
        </span>
      </div>

      <ul className="flex-1 py-4">
        {LINKS.map(({ href, label }) => {
          const active = path.startsWith(href)
          return (
            <li key={href}>
              <Link
                href={href}
                className={[
                  'block px-4 py-3 font-body text-sm border-l-2 transition-colors',
                  active
                    ? 'border-yellow text-yellow bg-yellow/5'
                    : 'border-transparent text-text-secondary hover:text-text-primary hover:border-border',
                ].join(' ')}
              >
                {label}
              </Link>
            </li>
          )
        })}
      </ul>

      <div className="p-4 border-t border-border text-text-secondary font-body" style={{ fontSize: '9px' }}>
        PROTECTED · CF ACCESS
      </div>
    </nav>
  )
}
