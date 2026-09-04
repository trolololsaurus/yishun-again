-- Migration 022: patterns table
-- Curated, operator-authored pattern pages (the public /patterns feature).
-- One row = one thesis page linking a hand-picked set of incidents.
-- incident_ids is set by hand when the row is written — it is NEVER grown
-- automatically. Run in Supabase SQL Editor.

CREATE TABLE IF NOT EXISTS patterns (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at    TIMESTAMPTZ DEFAULT NOW(),
  updated_at    TIMESTAMPTZ DEFAULT NOW(),
  slug          TEXT UNIQUE NOT NULL,
  title         TEXT NOT NULL,
  thesis        TEXT NOT NULL,        -- authored paragraph, the citable prose
  hero_image_url TEXT,
  incident_ids  UUID[] NOT NULL,      -- hand-picked, from discover_patterns output
  published     BOOLEAN NOT NULL DEFAULT FALSE
);

ALTER TABLE patterns ENABLE ROW LEVEL SECURITY;

CREATE POLICY "anon_read_published_patterns" ON patterns
  FOR SELECT TO anon USING (published = TRUE);

-- ── Seed: the four curated patterns (applied to prod 2026-09-04) ────────────
-- incident_ids are live UUIDs picked from discover output; a from-scratch
-- rebuild without those incidents will insert rows whose links resolve to
-- nothing until the incidents exist. ON CONFLICT keeps this re-runnable.
INSERT INTO patterns (slug, title, thesis, incident_ids, published) VALUES
(
  'yishun-cat-killings',
  'The Yishun Cat Killings',
  'If one place seeded Yishun''s national reputation, it was the cats. Since 2015 the estate has produced a grim, recurring genre of its own: community cats found strangled, thrown from blocks, sealed in rubbish chutes, and killed in numbers that turned dashcam appeals into news. It is the pattern that launched a thousand think-pieces about whether Yishun is "jinxed" — but the incidents behind the meme are real, individually reported, and disturbingly consistent. Collected here, they stop being a punchline and start being a record: the same crime, the same town, again and again, across a decade.',
  ARRAY['3752741a-9b96-4cbc-b1af-b2b35b1bd515','9dd5e3cb-cbc1-4d89-8dd2-c014290f7d90','edf191db-8bfa-43eb-bc38-e9fad3cc3723','d4efa377-412e-4044-ba16-2d41eb970ec5','b4a804d8-c495-44a6-b856-6a8111335e4b']::uuid[],
  TRUE
),
(
  'devils-ring-yishun-ring-road',
  'The Devil''s Ring: Yishun Ring Road',
  'One circular stretch of tarmac shows up in this archive more than anywhere else in the estate. We call it the Devil''s Ring, and we did not name it that to be dramatic — the data kept forcing our hand. Yishun Ring Road loops the older heart of the town, and along it cluster the estate''s gravest dagger incidents: a coffeeshop caretaker stabbed dead, a wife stabbed thirty times as her daughter watched, a lift-lobby murder, a worker crushed by a steel gate, and a run of falls from height that end at the foot of its blocks. These are the severity-5 cases — the ones that leave someone dead — and this single road holds a share of them wildly out of proportion to its length.',
  ARRAY['e7a8fa19-9c5a-4189-8346-53cf12fa7db5','3d33fa57-0a5e-4611-89b8-787a0bc21dfa','ec26a1ce-1686-442c-ac3d-6b62c03a7809','2f3979f4-79f7-438a-ad28-61c9f7d2f1ed','ac8197df-2fae-41b5-86ab-ce9405bc3ca8','bec083ae-5379-4305-9c7c-8bf1bb9aa93e','877c1f5f-b16a-4330-924d-fa7b02754176','afcfdaeb-487c-4064-be33-504b9d11d09f']::uuid[],
  TRUE
),
(
  'yishun-street-81',
  'Yishun Street 81',
  'Most Yishun patterns sprawl across the whole town. This one fits inside a few blocks. Yishun Street 81 — Blocks 839, 844, 874 and the courts and schools around them — has produced its own tight run of incidents: back-to-back HDB fires that sent residents to hospital and forced evacuations, a death inside a flat that the authorities had to publicly correct rumours about, a spent army cartridge found near a secondary school, and a child knocked down at a basketball court. No single event here is the estate''s worst, but the concentration is the point: one street name, one short window, and a caseload most streets never see.',
  ARRAY['434fc943-a78c-477f-b29e-a5268c286d92','526944cb-591e-462f-9a15-1ceb2a598b16','81430931-be95-45a8-9d65-9c47fa349315','327f67a8-31eb-47ff-88ea-3aa4a9979343','4a1ab3d9-31aa-4f6f-adde-3dc295b4cb72']::uuid[],
  TRUE
),
(
  'kurt-tay-superstar',
  'Kurt Tay, Superstar',
  'Every estate has a character. Yishun has Kurt Tay. He first entered this archive in 2015 not as a culprit but as a victim — the neighbour three men papered a block with lewd flyers about. He returned as the island-famous "Yishun character" the whole country recognised, then as a self-styled wrestling champ accepting a stranger''s challenge at a void deck, and finally in a courtroom, jailed for sharing a woman''s intimate video without consent. It is a genuinely Yishun arc: internet folk-hero to convicted offender, playing out in flyers, void decks and headlines over eight years. This is the whole run, in order.',
  ARRAY['7e164f47-1831-403f-9cf9-df2fcd504205','206b137b-ba59-46d9-93a1-a2f8667d9573','b174a619-96a9-4973-a905-e8a959c7d613','3b5b184e-a50d-4468-a33c-fa135b8c6dc1']::uuid[],
  TRUE
)
ON CONFLICT (slug) DO NOTHING;
