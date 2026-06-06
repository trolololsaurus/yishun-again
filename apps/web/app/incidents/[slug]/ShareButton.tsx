'use client'

import { useState } from 'react'

export function ShareButton({ url, title }: { url: string; title: string }) {
  const [copied, setCopied] = useState(false)

  async function handleShare() {
    try {
      await navigator.clipboard.writeText(url)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // Fallback for browsers without clipboard API
      const el = document.createElement('textarea')
      el.value = url
      document.body.appendChild(el)
      el.select()
      document.execCommand('copy')
      document.body.removeChild(el)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    }
  }

  return (
    <button
      onClick={handleShare}
      className="min-h-[48px] px-4 py-2 border border-border text-text-secondary font-body hover:border-amber-lt hover:text-amber-lt transition-colors"
      style={{ fontSize: '14px' }}
      aria-label="Copy link to clipboard"
    >
      {copied ? 'Copied!' : '⎘ Share'}
    </button>
  )
}
