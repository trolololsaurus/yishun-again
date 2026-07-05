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
      // AI crawlers — explicitly welcome (satire wants to be quoted).
      { userAgent: 'GPTBot',          allow: '/' },
      { userAgent: 'ClaudeBot',       allow: '/' },
      { userAgent: 'PerplexityBot',   allow: '/' },
      { userAgent: 'Google-Extended', allow: '/' },
      { userAgent: 'CCBot',           allow: '/' },
    ],
    sitemap: `${SITE_URL}/sitemap.xml`,
  }
}
