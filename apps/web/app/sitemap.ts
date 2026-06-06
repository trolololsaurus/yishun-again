import type { MetadataRoute } from 'next'
import { supabase } from '@/lib/supabase'

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? 'https://yishunagain.com'

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const { data: incidents } = await supabase
    .from('incidents')
    .select('slug,published_at')
    .eq('is_published', true)
    .order('published_at', { ascending: false })

  const incidentRoutes: MetadataRoute.Sitemap = (incidents ?? []).map(inc => ({
    url:             `${SITE_URL}/incidents/${inc.slug}`,
    lastModified:    new Date(inc.published_at),
    changeFrequency: 'weekly',
    priority:        0.8,
  }))

  return [
    {
      url:             SITE_URL,
      lastModified:    new Date(),
      changeFrequency: 'hourly',
      priority:        1.0,
    },
    {
      url:             `${SITE_URL}/timeline`,
      lastModified:    new Date(),
      changeFrequency: 'daily',
      priority:        0.5,
    },
    {
      url:             `${SITE_URL}/about`,
      lastModified:    new Date(),
      changeFrequency: 'monthly',
      priority:        0.5,
    },
    ...incidentRoutes,
  ]
}
