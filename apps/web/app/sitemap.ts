import type { MetadataRoute } from 'next'
import { supabase } from '@/lib/supabase'
import { SITE_URL } from '@/lib/site'

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const { data: incidents } = await supabase
    .from('incidents')
    .select('slug,incident_date,published_at')
    .eq('is_published', true)
    .order('incident_date', { ascending: false, nullsFirst: false })

  const incidentRoutes: MetadataRoute.Sitemap = (incidents ?? []).map(inc => {
    const modified = inc.published_at ?? inc.incident_date
    return {
      url:             `${SITE_URL}/incidents/${inc.slug}`,
      // new Date(null) is 1970-01-01 — omit lastModified when both dates are
      // null rather than advertise the epoch to crawlers.
      ...(modified ? { lastModified: new Date(modified) } : {}),
      changeFrequency: 'weekly' as const,
      priority:        0.8,
    }
  })

  return [
    {
      url:             `${SITE_URL}/`,  // trailing slash — matches the homepage canonical
      lastModified:    new Date(),
      changeFrequency: 'hourly',
      priority:        1.0,
    },
    {
      url:             `${SITE_URL}/timeline`,
      lastModified:    new Date(),
      changeFrequency: 'daily',
      priority:        0.6,
    },
    {
      url:             `${SITE_URL}/about`,
      lastModified:    new Date(),
      changeFrequency: 'monthly',
      priority:        0.6,
    },
    ...incidentRoutes,
  ]
}
