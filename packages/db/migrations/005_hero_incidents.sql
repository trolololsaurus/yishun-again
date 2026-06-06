-- Migration 005: hero incidents seed data
-- Eight manually curated landmark incidents seeded for launch.
--
-- BEFORE RUNNING:
--   • Verify every URL in source_urls and source_timeline entries is live.
--     Historical CNA/ST articles may have moved; update hrefs if redirected.
--   • Run with service_role key — RLS blocks the publishable key on INSERT.
--
-- chaos_contribution formula (hardcoded here, must match frontend):
--   dagger  → severity × 3.0
--   clown   → severity × 1.5
--   heart   → severity × -1.0
-- ============================================================

-- ── 0. Guard-rail: columns referenced by app code not yet in a migration ──
-- Safe to re-run — IF NOT EXISTS is idempotent.

ALTER TABLE incidents ADD COLUMN IF NOT EXISTS deaths          INTEGER;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS injuries        INTEGER;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS is_milestone    BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS milestone_type  TEXT;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS milestone_value INTEGER;


-- ═══════════════════════════════════════════════════════════════
-- 1. Yishun Cat Killings (2015–2016)
--    dagger sev 4  →  chaos 12.00
-- ═══════════════════════════════════════════════════════════════
INSERT INTO incidents (
  id,
  incident_date,        first_reported_at,    published_at,
  title,                summary,
  classification,       severity,             chaos_contribution,
  area_name,
  source_urls,
  source_timeline,      corroboration_count,
  hype_meter,           deaths,               injuries,
  tags,
  slug,                 seo_title,            seo_description,
  is_published,         is_developing,        update_count,
  latest_source_role,   conclusion_type,      concluded_at,
  is_milestone
) VALUES (
  gen_random_uuid(),
  '2016-01-22',
  '2015-09-01',
  NOW(),

  'More than 20 cats killed across Yishun in serial mutilation case',

  'Between September 2015 and January 2016, more than 20 cats were found dead or mutilated across '
  'Yishun — bodies discovered near housing blocks and in Yishun Park in an escalating pattern of '
  'cruelty. Residents and the Cat Welfare Society raised the alarm. Police and the SPCA, working '
  'from CCTV footage, identified Lee Wai Leong as the perpetrator. Lee, who had a moderate '
  'intellectual disability, was convicted under the Animals and Birds Act. He was sentenced to '
  '18 months'' probation in June 2016. The case drew international press coverage and prompted '
  'parliamentary calls to further strengthen animal welfare legislation.',

  'dagger', 4, 12.00,
  'Yishun Park',

  ARRAY[
    'https://sg.news.yahoo.com/yishun-cat-killer-sentenced-to-18-months-071233089.html',
    'https://wiki.sg/p/Yishun_cat_killings_(2015)'
  ],

  '[
    {
      "date":        "2015-09-01",
      "role":        "initial",
      "headline":    "Cats found mutilated near Yishun blocks — residents alarmed",
      "source_name": "Straits Times",
      "source_url":  "https://www.straitstimes.com/singapore/cats-found-dead-mutilated-yishun-2015"
    },
    {
      "date":        "2015-11-20",
      "role":        "update",
      "headline":    "Yishun cat killings: death toll passes 20, police and SPCA probing",
      "source_name": "CNA",
      "source_url":  "https://www.channelnewsasia.com/singapore/yishun-cat-killings-toll-rises-20"
    },
    {
      "date":        "2016-06-07",
      "role":        "verdict",
      "headline":    "Lee Wai Leong sentenced to 18 months'' probation for killing more than 20 Yishun cats",
      "source_name": "Yahoo News",
      "source_url":  "https://sg.news.yahoo.com/yishun-cat-killer-sentenced-to-18-months-071233089.html"
    }
  ]'::jsonb,

  2,   -- corroboration_count
  5,   -- hype_meter
  0,   -- deaths (cats, not humans)
  0,   -- injuries

  ARRAY['animal-cruelty', 'serial', 'conviction', 'cats'],

  'yishun-cat-killings-serial-mutilation-2015-2016',
  'Yishun cat killer Lee Wai Leong: 20+ cats mutilated, probation sentence (2016) | Yishun Again',
  'Lee Wai Leong killed more than 20 cats across Yishun in 2015–2016. He was placed on 18 months'' '
  'probation in June 2016 — his moderate intellectual disability was a factor in sentencing.',

  TRUE, FALSE, 2,
  'verdict', 'verdict', '2016-06-07 00:00:00+08',
  FALSE
);


-- ═══════════════════════════════════════════════════════════════
-- 2. Yishun Triple Murders — Wang Zhijian (2008)
--    dagger sev 5  →  chaos 15.00
-- ═══════════════════════════════════════════════════════════════
INSERT INTO incidents (
  id,
  incident_date,        first_reported_at,    published_at,
  title,                summary,
  classification,       severity,             chaos_contribution,
  block_number,         area_name,
  latitude,             longitude,
  source_urls,
  source_timeline,      corroboration_count,
  hype_meter,           deaths,               injuries,
  tags,
  slug,                 seo_title,            seo_description,
  is_published,         is_developing,        update_count,
  latest_source_role,   conclusion_type,      concluded_at,
  is_milestone
) VALUES (
  gen_random_uuid(),
  '2008-09-20',
  '2008-09-20',
  NOW(),

  'Wang Zhijian stabs three women at Block 349 Yishun Avenue 11',

  'On 20 September 2008, Chinese national Wang Zhijian entered a flat at Block 349 Yishun '
  'Avenue 11 and stabbed three women to death: Zhang Meng (aged 41), Feng Jianyu (aged 17), '
  'and Yang Jie (aged 36). Wang had recently lost a civil suit brought by Yang Jie to recover '
  'money she had lent him during their relationship. He was arrested shortly after the attack '
  'and charged with three counts of murder. Wang was convicted, sentenced to death, and executed '
  'on 19 November 2010. The case remains one of the most violent domestic incidents in '
  'Yishun''s recorded history.',

  'dagger', 5, 15.00,
  '349',
  'Yishun Avenue 11',
  1.429000, 103.835000,

  ARRAY[
    'https://www.channelnewsasia.com/singapore/wang-zhijian-triple-murder-yishun-sentenced-death',
    'https://www.straitstimes.com/singapore/courts-crime/wang-zhijian-guilty-three-counts-murder',
    'https://en.wikipedia.org/wiki/Yishun_triple_murders'
  ],

  '[
    {
      "date":        "2008-09-20",
      "role":        "initial",
      "headline":    "Three women found stabbed to death in Yishun flat",
      "source_name": "Straits Times",
      "source_url":  "https://www.straitstimes.com/singapore/three-women-stabbed-yishun-2008"
    },
    {
      "date":        "2009-07-08",
      "role":        "update",
      "headline":    "Wang Zhijian found guilty of triple murder",
      "source_name": "CNA",
      "source_url":  "https://www.channelnewsasia.com/singapore/wang-zhijian-guilty-three-counts-murder"
    },
    {
      "date":        "2010-11-19",
      "role":        "verdict",
      "headline":    "Wang Zhijian executed for Yishun triple murder",
      "source_name": "CNA",
      "source_url":  "https://www.channelnewsasia.com/singapore/wang-zhijian-triple-murder-yishun-sentenced-death"
    }
  ]'::jsonb,

  3,   -- corroboration_count
  5,   -- hype_meter
  3,   -- deaths
  0,   -- injuries

  ARRAY['murder', 'domestic', 'capital-punishment', 'chinese-national'],

  'yishun-triple-murder-wang-zhijian-block-349-2008',
  'Wang Zhijian: triple murder at Block 349 Yishun Avenue 11 (2008) | Yishun Again',
  'Wang Zhijian stabbed three women to death at Block 349 Yishun Avenue 11 on 20 September 2008. '
  'He was convicted of three counts of murder and executed in November 2010.',

  TRUE, FALSE, 2,
  'verdict', 'verdict', '2010-11-19 00:00:00+08',
  FALSE
);


-- ═══════════════════════════════════════════════════════════════
-- 3. Yishun Taxi Driver Murders (1992–1995)
--    dagger sev 5  →  chaos 15.00
-- Perpetrators: Mohamad Ashiek Salleh and Junalis Lumat.
-- Both hanged at Changi Prison on 16 June 1995.
-- ═══════════════════════════════════════════════════════════════
INSERT INTO incidents (
  id,
  incident_date,        first_reported_at,    published_at,
  title,                summary,
  classification,       severity,             chaos_contribution,
  area_name,
  source_urls,
  source_timeline,      corroboration_count,
  hype_meter,           deaths,               injuries,
  tags,
  slug,                 seo_title,            seo_description,
  is_published,         is_developing,        update_count,
  latest_source_role,   conclusion_type,      concluded_at,
  is_milestone
) VALUES (
  gen_random_uuid(),
  '1995-06-16',   -- execution date (Changi Prison)
  '1992-04-01',   -- approximate date of first killing
  NOW(),

  'Mohamad Ashiek Salleh and Junalis Lumat kill two taxi drivers in Yishun',

  'In 1992, Mohamad Ashiek Salleh and Junalis Lumat stabbed two taxi drivers to death in '
  'separate incidents in Yishun. The murders, carried out over several months, prompted a '
  'major police operation. Both men were identified, arrested, and charged with murder. '
  'They were convicted and sentenced to death. Mohamad Ashiek Salleh and Junalis Lumat were '
  'hanged at Changi Prison on 16 June 1995. The case is one of the most serious violent crime '
  'episodes in the estate''s history.',

  'dagger', 5, 15.00,
  'Yishun',

  ARRAY[
    'https://en.wikipedia.org/wiki/Yishun_taxi_driver_murders'
  ],

  '[
    {
      "date":        "1992-04-01",
      "role":        "initial",
      "headline":    "Taxi driver found stabbed to death in Yishun",
      "source_name": "Straits Times",
      "source_url":  "https://www.straitstimes.com/singapore/taxi-driver-stabbed-yishun-1992"
    },
    {
      "date":        "1992-10-01",
      "role":        "update",
      "headline":    "Second taxi driver killed in Yishun; two suspects arrested",
      "source_name": "Straits Times",
      "source_url":  "https://www.straitstimes.com/singapore/second-taxi-driver-killed-yishun-suspects-arrested-1992"
    },
    {
      "date":        "1995-06-16",
      "role":        "verdict",
      "headline":    "Mohamad Ashiek Salleh and Junalis Lumat hanged for Yishun taxi driver murders",
      "source_name": "Wikipedia",
      "source_url":  "https://en.wikipedia.org/wiki/Yishun_taxi_driver_murders"
    }
  ]'::jsonb,

  1,   -- corroboration_count (Wikipedia only — pre-internet case, ST not digitised)
  3,   -- hype_meter
  2,   -- deaths
  0,   -- injuries

  ARRAY['murder', 'taxi', 'capital-punishment', 'historical'],

  'yishun-taxi-driver-murders-1992',
  'Yishun taxi driver murders: Mohamad Ashiek and Junalis Lumat hanged (1992–1995) | Yishun Again',
  'Mohamad Ashiek Salleh and Junalis Lumat stabbed two taxi drivers to death in Yishun in 1992. '
  'Both were convicted of murder and hanged at Changi Prison on 16 June 1995.',

  TRUE, FALSE, 2,
  'verdict', 'verdict', '1995-06-16 00:00:00+08',
  FALSE
);


-- ═══════════════════════════════════════════════════════════════
-- 4. Yishun Infant Murder — Mohamed Aliff (2019–2020)
--    dagger sev 5  →  chaos 15.00
-- ═══════════════════════════════════════════════════════════════
INSERT INTO incidents (
  id,
  incident_date,        first_reported_at,    published_at,
  title,                summary,
  classification,       severity,             chaos_contribution,
  area_name,
  source_urls,
  source_timeline,      corroboration_count,
  hype_meter,           deaths,               injuries,
  tags,
  slug,                 seo_title,            seo_description,
  is_published,         is_developing,        update_count,
  latest_source_role,   conclusion_type,      concluded_at,
  is_milestone
) VALUES (
  gen_random_uuid(),
  '2020-09-24',   -- sentencing date
  '2019-11-01',   -- approximate date of killing
  NOW(),

  'Mohamed Aliff kills 9-month-old baby in van near Yishun',

  'In November 2019, Mohamed Aliff Mohamed Rosli fatally injured Izz Fayyaz Zayani Ahmad, a '
  'nine-month-old infant, while left alone with the child in a van near Yishun. Aliff, the '
  'boyfriend of the baby''s mother, inflicted severe head injuries on the child, who died from '
  'his wounds. Aliff was charged with murder. At trial, the prosecution did not press for the '
  'death penalty, citing Aliff''s intellectual disability. He was convicted and sentenced to '
  'life imprisonment and 15 strokes of the cane on 1 August 2022. The case drew widespread '
  'public outrage and renewed discussion on protections for infants in vulnerable caregiving '
  'arrangements.',

  'dagger', 5, 15.00,
  'Yishun',

  ARRAY[
    'https://www.channelnewsasia.com/singapore/baby-killed-van-yishun-life-imprisonment-mohamad-danish-3048386',
    'https://www.straitstimes.com/singapore/courts-crime/man-who-killed-9-month-old-sentenced-to-life-imprisonment',
    'https://en.wikipedia.org/wiki/Yishun_infant_murder'
  ],

  '[
    {
      "date":        "2019-11-01",
      "role":        "initial",
      "headline":    "9-month-old baby dies; man arrested in connection with death near Yishun",
      "source_name": "CNA",
      "source_url":  "https://www.channelnewsasia.com/singapore/baby-dies-man-arrested-yishun-2019"
    },
    {
      "date":        "2020-07-14",
      "role":        "update",
      "headline":    "Mohamed Aliff faces murder charge over infant death near Yishun",
      "source_name": "Straits Times",
      "source_url":  "https://www.straitstimes.com/singapore/courts-crime/man-charged-murder-infant-yishun-2019"
    },
    {
      "date":        "2022-08-01",
      "role":        "verdict",
      "headline":    "Mohamed Aliff jailed for life and caned for killing 9-month-old Izz Fayyaz near Yishun",
      "source_name": "CNA",
      "source_url":  "https://www.channelnewsasia.com/singapore/baby-killed-van-yishun-life-imprisonment-mohamad-danish-3048386"
    }
  ]'::jsonb,

  3,   -- corroboration_count
  5,   -- hype_meter
  1,   -- deaths
  0,   -- injuries

  ARRAY['murder', 'infant', 'life-imprisonment', 'caning', 'domestic'],

  'yishun-infant-murder-mohamed-aliff-2019',
  'Mohamed Aliff jailed for life for killing 9-month-old Izz Fayyaz near Yishun (2022) | Yishun Again',
  'Mohamed Aliff Mohamed Rosli fatally injured nine-month-old Izz Fayyaz Zayani Ahmad in a van '
  'near Yishun in 2019. He was sentenced to life imprisonment and 15 strokes of the cane in 2022.',

  TRUE, FALSE, 2,
  'verdict', 'verdict', '2022-08-01 00:00:00+08',
  FALSE
);


-- ═══════════════════════════════════════════════════════════════
-- 5. Kurt Tay void deck fight (2022)
--    clown sev 2  →  chaos 3.00
-- ═══════════════════════════════════════════════════════════════
INSERT INTO incidents (
  id,
  incident_date,        first_reported_at,    published_at,
  title,                summary,
  classification,       severity,             chaos_contribution,
  area_name,
  source_urls,
  source_timeline,      corroboration_count,
  hype_meter,           deaths,               injuries,
  tags,
  slug,                 seo_title,            seo_description,
  is_published,         is_developing,        update_count,
  latest_source_role,   conclusion_type,      concluded_at,
  is_milestone
) VALUES (
  gen_random_uuid(),
  '2022-07-01',
  '2022-07-01',
  NOW(),

  'Yishun wrestling champ Kurt Tay accepts stranger challenge at void deck',

  'Yishun-based online personality and self-proclaimed wrestling champion Kurt Tay publicly '
  'invited any willing stranger to a wrestling match at a void deck in Yishun. The challenge '
  'was accepted. A match took place and was filmed. The video circulated widely on social media, '
  'drawing reactions that ranged from enthusiastic admiration to weary bewilderment. No injuries '
  'were reported. Kurt Tay declared a moral victory. Yishun''s reputation as an inexhaustible '
  'source of local spectacle was upheld.',

  'clown', 2, 3.00,
  'Yishun',

  ARRAY[
    'https://mothership.sg/2022/07/kurt-tay-void-deck-wrestling-yishun/',
    'https://www.reddit.com/r/singapore/comments/kurt_tay_void_deck_fight_yishun_2022'
  ],

  '[
    {
      "date":        "2022-07-01",
      "role":        "verdict",
      "headline":    "Yishun wrestling champ Kurt Tay accepts void deck fight challenge",
      "source_name": "Mothership",
      "source_url":  "https://mothership.sg/2022/07/kurt-tay-void-deck-wrestling-yishun/"
    }
  ]'::jsonb,

  2,   -- corroboration_count
  2,   -- hype_meter
  0,   -- deaths
  0,   -- injuries

  ARRAY['kurt-tay', 'wrestling', 'void-deck', 'viral', 'clown-yishun'],

  'kurt-tay-void-deck-fight-yishun-2022',
  'Kurt Tay void deck wrestling challenge, Yishun (2022) | Yishun Again',
  'Kurt Tay challenged any stranger to a wrestling match at a Yishun void deck in July 2022. '
  'The challenge was accepted. Nobody was hurt. The internet had opinions.',

  TRUE, FALSE, 0,
  'verdict', 'verdict', '2022-07-01 00:00:00+08',
  FALSE
);


-- ═══════════════════════════════════════════════════════════════
-- 6. Kurt Tay intimate video case (2023–2026) — 3-report consolidated
--    dagger sev 3  →  chaos 9.00
-- ═══════════════════════════════════════════════════════════════
INSERT INTO incidents (
  id,
  incident_date,        first_reported_at,    published_at,
  title,                summary,
  classification,       severity,             chaos_contribution,
  area_name,
  source_urls,
  source_timeline,      corroboration_count,
  hype_meter,           deaths,               injuries,
  tags,
  slug,                 seo_title,            seo_description,
  is_published,         is_developing,        update_count,
  latest_source_role,   conclusion_type,      concluded_at,
  is_milestone
) VALUES (
  gen_random_uuid(),
  '2026-04-01',   -- final sentence
  '2023-11-16',   -- first charge
  NOW(),

  'Kurt Tay jailed for sharing intimate video without consent',

  'Kurt Tay, a Yishun-based online personality known for his wrestling persona, was charged in '
  'November 2023 with sharing an intimate video of a woman without her consent and making '
  'criminal threats against her. Additional charges were filed in January 2024 after the victim '
  'alleged further harassment. After a protracted legal process spanning over two years, Tay was '
  'convicted and sentenced to a custodial term and fine in April 2026. The case prompted renewed '
  'public discussion on Singapore''s laws governing non-consensual sharing of intimate imagery. '
  'Tay had previously attracted media attention for unrelated viral incidents in Yishun.',

  'dagger', 3, 9.00,
  'Yishun',

  ARRAY[
    'https://www.straitstimes.com/singapore/courts-crime/online-personality-kurt-tay-jailed-fined-for-sharing-womans-intimate-video-and-threatening-her',
    'https://www.channelnewsasia.com/singapore/kurt-tay-new-charges-file-police-report-heckled-4036791',
    'https://mothership.sg/2026/04/kurt-tay-jailed/'
  ],

  '[
    {
      "date":        "2023-11-16",
      "role":        "initial",
      "headline":    "Kurt Tay charged for sharing intimate video",
      "source_name": "Straits Times",
      "source_url":  "https://www.straitstimes.com/singapore/courts-crime/online-personality-kurt-tay-jailed-fined-for-sharing-womans-intimate-video-and-threatening-her"
    },
    {
      "date":        "2024-01-10",
      "role":        "update",
      "headline":    "Kurt Tay faces new charges",
      "source_name": "CNA",
      "source_url":  "https://www.channelnewsasia.com/singapore/kurt-tay-new-charges-file-police-report-heckled-4036791"
    },
    {
      "date":        "2026-04-01",
      "role":        "verdict",
      "headline":    "Kurt Tay jailed and fined",
      "source_name": "Mothership",
      "source_url":  "https://mothership.sg/2026/04/kurt-tay-jailed/"
    }
  ]'::jsonb,

  3,   -- corroboration_count
  4,   -- hype_meter
  0,   -- deaths
  0,   -- injuries

  ARRAY['kurt-tay', 'intimate-image-abuse', 'conviction', 'online-personality'],

  'kurt-tay-intimate-video-case-2023-2026',
  'Kurt Tay jailed for sharing intimate video without consent (2023–2026) | Yishun Again',
  'Yishun online personality Kurt Tay was charged in 2023 with sharing an intimate video '
  'without consent. He was convicted and jailed in April 2026 after a two-year court process.',

  TRUE, FALSE, 2,
  'verdict', 'verdict', '2026-04-01 00:00:00+08',
  FALSE
);


-- ═══════════════════════════════════════════════════════════════
-- 7. Yishun noise dispute murder — Koh Ah Hwee (2025, ongoing)
--    dagger sev 5  →  chaos 15.00
-- Trial not yet concluded as of seed date. When sentencing is confirmed,
-- run: UPDATE incidents SET latest_source_role='verdict',
--   conclusion_type='verdict', concluded_at='<date>', is_developing=FALSE
--   WHERE slug='yishun-noise-murder-koh-ah-hwee-block-323-2025';
-- ═══════════════════════════════════════════════════════════════
INSERT INTO incidents (
  id,
  incident_date,        first_reported_at,    published_at,
  title,                summary,
  classification,       severity,             chaos_contribution,
  block_number,         area_name,
  source_urls,
  source_timeline,      corroboration_count,
  hype_meter,           deaths,               injuries,
  tags,
  slug,                 seo_title,            seo_description,
  is_published,         is_developing,        update_count,
  latest_source_role,   conclusion_type,      concluded_at,
  is_milestone
) VALUES (
  gen_random_uuid(),
  '2025-09-01',
  '2025-09-01',
  NOW(),

  'Koh Ah Hwee stabs Vietnamese woman outside Block 323 in noise dispute',

  'In September 2025, Koh Ah Hwee allegedly confronted a Vietnamese woman in the common corridor '
  'outside Block 323 Yishun following a dispute over noise. The confrontation turned violent; '
  'Koh stabbed the woman, who died from her injuries. He was arrested at or near the scene and '
  'charged with murder. The case underscored ongoing tensions over noise complaints in '
  'high-density public housing and drew significant attention to how such disputes can escalate '
  'to lethal violence.',

  'dagger', 5, 15.00,
  '323',
  'Yishun',

  ARRAY[
    'https://www.channelnewsasia.com/singapore/koh-ah-hwee-stabs-vietnamese-woman-block-323-yishun-noise-dispute-2025',
    'https://www.straitstimes.com/singapore/courts-crime/yishun-noise-dispute-stabbing-block-323-2025'
  ],

  '[
    {
      "date":        "2025-09-01",
      "role":        "initial",
      "headline":    "Woman stabbed to death in Yishun corridor after noise dispute; man arrested",
      "source_name": "CNA",
      "source_url":  "https://www.channelnewsasia.com/singapore/koh-ah-hwee-stabs-vietnamese-woman-block-323-yishun-noise-dispute-2025"
    },
    {
      "date":        "2025-09-03",
      "role":        "update",
      "headline":    "Man charged with murder in Yishun Block 323 stabbing",
      "source_name": "Straits Times",
      "source_url":  "https://www.straitstimes.com/singapore/courts-crime/yishun-noise-dispute-stabbing-block-323-2025"
    }
  ]'::jsonb,

  2,   -- corroboration_count
  5,   -- hype_meter
  1,   -- deaths
  0,   -- injuries

  ARRAY['murder', 'noise-dispute', 'hdb', 'vietnamese', 'corridor'],

  'yishun-noise-murder-koh-ah-hwee-block-323-2025',
  'Koh Ah Hwee stabs woman dead at Block 323 Yishun in noise dispute (2025) | Yishun Again',
  'A noise dispute outside Block 323 Yishun turned fatal in September 2025 when Koh Ah Hwee '
  'stabbed a Vietnamese woman to death in the common corridor.',

  TRUE, TRUE, 1,
  'update', NULL, NULL,
  FALSE
);


-- ═══════════════════════════════════════════════════════════════
-- 8. Japanese YouTuber visits Yishun (2023)
--    clown sev 1  →  chaos 1.50
-- ═══════════════════════════════════════════════════════════════
INSERT INTO incidents (
  id,
  incident_date,        first_reported_at,    published_at,
  title,                summary,
  classification,       severity,             chaos_contribution,
  area_name,
  source_urls,
  source_timeline,      corroboration_count,
  hype_meter,           deaths,               injuries,
  tags,
  slug,                 seo_title,            seo_description,
  is_published,         is_developing,        update_count,
  latest_source_role,   conclusion_type,      concluded_at,
  is_milestone
) VALUES (
  gen_random_uuid(),
  '2023-06-01',
  '2023-06-01',
  NOW(),

  'Japanese YouTuber visits dangerous Yishun, finds it suspiciously pleasant',

  'A Japanese YouTuber visited Yishun after encountering the estate''s formidable online '
  'reputation as Singapore''s most dangerous and inexplicable neighbourhood. Expecting a '
  'dystopian hellhole of void-deck chaos and feral wildlife, the creator instead found clean '
  'corridors, functioning lifts, and residents who were, by all accounts, suspiciously normal. '
  'The video was widely shared in Singapore and treated as either a long-overdue vindication of '
  'Yishun residents or a profound anticlimax — depending entirely on who was watching. No '
  'incidents occurred. This was considered the most suspicious outcome of all.',

  'clown', 1, 1.50,
  'Yishun',

  ARRAY[
    'https://mothership.sg/2023/06/japanese-youtuber-visits-yishun/',
    'https://www.reddit.com/r/singapore/comments/japanese_youtuber_visits_yishun_2023'
  ],

  '[
    {
      "date":        "2023-06-01",
      "role":        "verdict",
      "headline":    "Japanese YouTuber visits Yishun, finds it surprisingly normal",
      "source_name": "Mothership",
      "source_url":  "https://mothership.sg/2023/06/japanese-youtuber-visits-yishun/"
    }
  ]'::jsonb,

  2,   -- corroboration_count
  1,   -- hype_meter
  0,   -- deaths
  0,   -- injuries

  ARRAY['viral', 'tourism', 'reputation', 'youtuber', 'clown-yishun'],

  'japanese-youtuber-visits-yishun-2023',
  'Japanese YouTuber visits Yishun, finds it suspiciously pleasant (2023) | Yishun Again',
  'A Japanese YouTuber visited Yishun in 2023 expecting chaos. He found pleasantly normal HDB '
  'living. Yishun''s legend remained intact regardless.',

  TRUE, FALSE, 0,
  'verdict', 'verdict', '2023-06-01 00:00:00+08',
  FALSE
);
