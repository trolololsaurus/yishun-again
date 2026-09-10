import type { MetadataRoute } from 'next'
import { SITE_URL } from '@/lib/site'

export default function robots(): MetadataRoute.Robots {
  return {
    rules: [
      {
        userAgent: '*',
        allow:     '/',
        disallow:  '/api/',
      },
      // AI answer/search crawlers (citation eligibility) — explicitly welcome,
      // separate from the training crawlers below: satire wants to be quoted.
      { userAgent: 'OAI-SearchBot',    allow: '/' },  // ChatGPT search
      { userAgent: 'ChatGPT-User',     allow: '/' },  // ChatGPT live fetch
      { userAgent: 'Claude-SearchBot', allow: '/' },  // Claude search
      { userAgent: 'Claude-User',      allow: '/' },  // Claude live fetch
      { userAgent: 'PerplexityBot',    allow: '/' },  // Perplexity search
      { userAgent: 'Perplexity-User',  allow: '/' },  // Perplexity live fetch
      // AI training crawlers — also allowed: broader model exposure over time
      // is a reasonable trade for a visibility-driven project.
      { userAgent: 'GPTBot',              allow: '/' },
      { userAgent: 'ClaudeBot',           allow: '/' },
      { userAgent: 'anthropic-ai',        allow: '/' },
      { userAgent: 'Google-Extended',     allow: '/' },
      { userAgent: 'CCBot',               allow: '/' },
      { userAgent: 'Bytespider',          allow: '/' },
      { userAgent: 'meta-externalagent',  allow: '/' },
      { userAgent: 'Amazonbot',           allow: '/' },
      { userAgent: 'Applebot-Extended',   allow: '/' },
    ],
    sitemap: `${SITE_URL}/sitemap.xml`,
  }
}
