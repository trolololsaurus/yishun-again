import type { MetadataRoute } from 'next'
import { supabase } from '@/lib/supabase'
import { SITE_URL } from '@/lib/site'

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const { data: incidents } = await supabase
    .from('incidents')
    .select('slug,incident_date,published_at')
    .eq('is_published', true)
    .order('incident_date', { ascending: false, nullsFirst: false })

  const incidentRoutes: MetadataRoute.Sitemap = (incidents ?? []).map(inc => ({
    url:             `${SITE_URL}/incidents/${inc.slug}`,
    lastModified:    new Date(inc.published_at ?? inc.incident_date),
    changeFrequency: 'weekly',
    priority:        0.8,
  }))

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
