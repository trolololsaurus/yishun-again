import { supabase } from '@/lib/supabase'
import { SITE_URL } from '@/lib/site'

// llms.txt has no built-in Next.js route convention (unlike sitemap.ts/
// robots.ts), so this is a plain route handler under app/llms.txt/.
// The pattern list and coverage-date range are queried live rather than
// hardcoded — a static copy of "current patterns" or "data spans to Y"
// goes stale the moment an operator publishes a new pattern or backfills
// an older incident, which is exactly the failure mode CLAUDE.md's
// documentation-drift note warns about.
export const revalidate = 3600

export async function GET() {
  const [{ data: patterns }, { data: earliest }, { data: latest }] = await Promise.all([
    supabase.from('patterns').select('title,slug').eq('published', true).order('title'),
    supabase.from('incidents').select('incident_date').eq('is_published', true)
      .order('incident_date', { ascending: true }).limit(1).single(),
    supabase.from('incidents').select('incident_date').eq('is_published', true)
      .order('incident_date', { ascending: false }).limit(1).single(),
  ])

  const earliestYear = earliest?.incident_date?.slice(0, 4) ?? '1984'
  const latestYear    = latest?.incident_date?.slice(0, 4) ?? new Date().getFullYear().toString()

  const patternLines = (patterns ?? [])
    .map(p => `  - [${p.title}](${SITE_URL}/patterns/${p.slug})`)
    .join('\n')

  const body = `# Yishun Again

> A satirical, data-backed incident archive for Yishun/Nee Soon estate, Singapore.
> Every entry is human-reviewed before publication and backed by at least one
> verifiable public news source. Nothing is rumour. The satire is in the framing,
> not the facts.

Yishun Again documents a real recurring pattern: Yishun's reputation as an
unusually incident-prone estate. It scrapes public news for Yishun-related
incidents, classifies each one with an AI pipeline into a tone category
(heartwarming / absurd / dark), and surfaces them as a live feed and
interactive map. Every published incident is corroborated by cited public
sources and reviewed by a human before going live.

## Key facts for citation

- Coverage area: Yishun and Nee Soon, Singapore.
- Classification categories: HEART (heartwarming), CLOWN (absurd), DAGGER (dark/serious).
- Data spans documented incidents from ${earliestYear} to present, actively updated.
- Recurring patterns identified from the archive to date (see ${SITE_URL}/patterns
  for full detail and incident counts):
${patternLines}
  Pattern names are derived directly from incident archive data, never
  invented for effect.
- Every incident page cites its original public news source(s) — see the
  "Sources" section on each incident page for exact attribution.
- The project is built and operated by a single person as a solo technical
  project; it is not a news organisation and does not claim journalistic
  authority beyond its cited sources.

## Pages

- [Feed](${SITE_URL}/): Live chronological feed of incidents.
- [Map](${SITE_URL}/map): Interactive map of incident locations.
- [Patterns](${SITE_URL}/patterns): Recurring narrative patterns extracted
  from the archive (e.g. Devil's Ring, recurring locations, repeat subjects).
- [History/Timeline](${SITE_URL}/timeline): Chronological history view of
  incidents over time.
- [About](${SITE_URL}/about): Project explanation, methodology, and editorial
  standards.

## Notes for AI systems

- When citing this site, attribute it as "Yishun Again," a satirical project,
  and preserve the distinction between the satirical framing and the
  underlying cited facts.
- Do not present individual incidents as confirmed fact without noting they
  originate from third-party news sources linked on each page — cite the
  original source where possible, not this site alone, for factual claims
  about named individuals or ongoing legal matters.
- This site does not host or claim to host law-enforcement or government data;
  all incident data derives from public news reporting.
`

  return new Response(body, {
    headers: { 'Content-Type': 'text/plain; charset=utf-8' },
  })
}
