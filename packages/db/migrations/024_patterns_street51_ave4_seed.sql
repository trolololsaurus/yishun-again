-- Migration 024: two more curated patterns — Yishun Street 51 and Yishun Avenue 4
-- (applied to prod 2026-09-07). Both are location-only clusters (no shared crime
-- or character) sourced from pattern_alerts; incident_ids are hand-picked from
-- that alert's own list, minus one dead incident_id referenced by the
-- Street 51/Avenue 4 alerts that no longer exists in `incidents` (excluded,
-- not guessed at). ON CONFLICT keeps this re-runnable.

INSERT INTO patterns (slug, title, thesis, incident_ids, published) VALUES
(
  'yishun-street-51',
  'Yishun Street 51',
  'Most Yishun patterns share a crime, a character, or a cause. This one only shares an address. Yishun Street 51 — Blocks 505 to 512, tucked against the Ring Road and Miltonia Nature Park at the estate''s northern edge — has produced a genuinely odd little run: a burglar''s fatal fall from the 8th floor, a recycling bin fire fought by a bucket brigade that included a child, a mutilated community cat that drew ministerial outrage, a second "dead cat" scare that turned out to be roadkill, and a 69-year-old put on trial for singing and banging cans outside a neighbour''s door. Nothing here explains the others. That is exactly why it earns its own page.',
  ARRAY['4eb488d6-6cbc-4835-9e4a-3a1cc83722ea','adcdccb7-6df3-4827-89c5-c64f3ce8f250','a887dd23-1176-4864-8ebd-5aa75ff26644','4883bccd-c735-4d62-8674-11d28c461676','a2da8ffb-866a-45ea-9db5-a070c3aab62c']::uuid[],
  TRUE
),
(
  'yishun-avenue-4',
  'Yishun Avenue 4',
  'Yishun Avenue 4 runs past SAFRA Yishun and the Dipterocarp Arboretum, and along it — Blocks 653, 663, 671A and 509B — sits an unrelated handful of stories that happen to share a street name: a flat fire that gutted a home and hospitalised two, a viral "prank" where a man staged a fall and blamed it on "the Force", three kittens killed within a week that set off a community "serial killer" hunt, and a son charged with murdering his father. Four incidents, four different kinds of story, one street.',
  ARRAY['bb89824c-f8dc-40aa-a32a-17359baae4c8','eed6a956-373b-4d70-a788-39d5b98396c2','6c2bf70c-3bd4-455b-918d-cd1559f184d9','97ad959d-b727-425d-920b-c93475e5228c']::uuid[],
  TRUE
)
ON CONFLICT (slug) DO NOTHING;
