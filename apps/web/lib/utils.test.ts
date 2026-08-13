/**
 * Regression guards for the pure display helpers in lib/utils.ts.
 *
 * Run:  npm test          (from apps/web)
 *       node --test lib/
 *
 * No test framework is installed — this uses node:test and Node 24's native
 * TypeScript stripping, which is why the import below carries a .ts extension.
 *
 * These three functions decide what the incident page SAYS about a story, so a
 * silent break here is a factual error on a published page rather than a
 * cosmetic one. Each case below is taken from real live data.
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'

import { sharedLocationLabel, dateFromUrl, toParagraphs, splitSentences, canonicalUrl, uniqueSources, foreignSourceNote, spreadOverlappingPins, PIN_SPREAD_DEG } from './utils.ts'

// ── sharedLocationLabel ─────────────────────────────────────────────────────
// "Same location" alone is meaningless in a single-town archive. All 137
// confirmed same_location links in the live DB agree on area_name.

test('shared location: same street, different blocks → the street', () => {
  assert.equal(
    sharedLocationLabel(
      { area_name: 'Yishun Ring Road', block_number: '243' },
      { area_name: 'Yishun Ring Road', block_number: '342B' }
    ),
    'Yishun Ring Road'
  )
})

test('shared location: same block AND street → block-level, the most specific', () => {
  assert.equal(
    sharedLocationLabel(
      { area_name: 'Yishun Ring Road', block_number: '803' },
      { area_name: 'Yishun Ring Road', block_number: '803' }
    ),
    'Block 803, Yishun Ring Road'
  )
})

test('shared location: a shared block rescues a generic area', () => {
  assert.equal(
    sharedLocationLabel(
      { area_name: 'Yishun', block_number: '128' },
      { area_name: 'Yishun', block_number: '128' }
    ),
    'Block 128, Yishun'
  )
})

test('shared location: "Yishun" alone is NOT a location — returns null', () => {
  // 92 of 163 published incidents have area_name='Yishun'. Printing
  // "Same location — Yishun" for those would be noise, not information.
  assert.equal(
    sharedLocationLabel(
      { area_name: 'Yishun', block_number: null },
      { area_name: 'Yishun', block_number: null }
    ),
    null
  )
})

test('shared location: different streets share nothing', () => {
  assert.equal(
    sharedLocationLabel(
      { area_name: 'Yishun Ring Road', block_number: '243' },
      { area_name: 'Yishun Street 22',  block_number: '279' }
    ),
    null
  )
})

test('shared location: casing and spacing do not defeat the match', () => {
  assert.equal(
    sharedLocationLabel(
      { area_name: 'Yishun Ring Road',   block_number: null },
      { area_name: 'yishun  ring   road', block_number: null }
    ),
    'Yishun Ring Road'
  )
})

test('shared location: nulls and empty strings never match each other', () => {
  assert.equal(sharedLocationLabel(
    { area_name: null, block_number: null }, { area_name: null, block_number: null }), null)
  assert.equal(sharedLocationLabel(
    { area_name: '', block_number: '' }, { area_name: '', block_number: '' }), null)
})

test('shared location: a non-numeric block is not prefixed with "Block"', () => {
  assert.equal(
    sharedLocationLabel(
      { area_name: 'Yishun', block_number: 'Northpoint City' },
      { area_name: 'Yishun', block_number: 'Northpoint City' }
    ),
    'Northpoint City, Yishun'
  )
})

// ── dateFromUrl ─────────────────────────────────────────────────────────────
// A display fallback only, for links with no source_timeline entry. It must
// never invent a date: a wrong date beside a citation is a factual error.

test('date from url: path date (malaymail)', () => {
  assert.equal(
    dateFromUrl('https://www.malaymail.com/news/world/2018/07/13/singaporean-jailed-10-years/1651822'),
    '2018-07-13'
  )
})

test('date from url: zaobao storyYYYYMMDD', () => {
  assert.equal(
    dateFromUrl('https://www.zaobao.com.sg/news/singapore/story20260725-9419022'),
    '2026-07-25'
  )
})

test('date from url: mothership /2026/07/', () => {
  // Month-only path: no day component, so this must NOT resolve to a date.
  assert.equal(dateFromUrl('https://mothership.sg/2026/07/yishun-man-spat-to-be-charged/'), null)
})

test('date from url: publishers with no date in the path return null', () => {
  // ST, CNA and Yahoo — the tool that resolves these is
  // packages/agents/tools/backfill_source_dates.py, which fetches the article.
  for (const url of [
    'https://www.straitstimes.com/singapore/courts-crime/man-charged-with-wifes-murder-in-yishun',
    'https://www.channelnewsasia.com/singapore/man-jailed-10-years-fatally-stabbing-wife-more-30-times-5729616',
    'https://sg.news.yahoo.com/man-arrested-over-murder-at-yishun-ring-road-071843684.html',
    'https://www.asiaone.com/singapore/yishun-hdb-void-deck-seat-man-found-dead',
  ]) {
    assert.equal(dateFromUrl(url), null, url)
  }
})

test('date from url: calendar-invalid dates are rejected, not rolled over', () => {
  // new Date('2026-02-31') silently becomes 2026-03-03.
  assert.equal(dateFromUrl('https://example.com/2026/02/31/story'), null)
  assert.equal(dateFromUrl('https://example.com/2026/13/01/story'), null)
})

test('date from url: junk input never throws and never guesses', () => {
  assert.equal(dateFromUrl(null), null)
  assert.equal(dateFromUrl(''), null)
  assert.equal(dateFromUrl('not a url at all'), null)
  assert.equal(dateFromUrl('/2018/07/13/relative-path'), null)
})

test('date from url: a query string cannot supply the date', () => {
  // Only the pathname is read — ?ref=2020/01/01 is not a publication date.
  assert.equal(dateFromUrl('https://example.com/article?ref=2020/01/01'), null)
})

// ── splitSentences / toParagraphs ───────────────────────────────────────────
// Formatting only. Must never add, drop or reorder a word.

test('sentences: a decimal time is not a sentence break', () => {
  // Real text from yishun-ring-road-killing-sri-idayu-2016.
  const s = splitSentences('She was pronounced dead at 4.26am the following morning. Police arrived.')
  assert.equal(s.length, 2)
})

test('sentences: an abbreviation is not a sentence break', () => {
  assert.equal(splitSentences('Mr. Tan called the police. They arrived within minutes.').length, 2)
  assert.equal(splitSentences('The unit at Blk 279 St. 22 was sealed. Nobody was home.').length, 2)
})

test('paragraphs: author blank lines win', () => {
  assert.deepEqual(toParagraphs('First para.\n\nSecond para.'), ['First para.', 'Second para.'])
})

test('paragraphs: a short summary stays a single paragraph', () => {
  const s = 'A man was taken to hospital. Police are investigating.'
  assert.deepEqual(toParagraphs(s), [s])
})

test('paragraphs: a long unbroken summary is split, losing nothing', () => {
  // Shape of the 163/163 live rows: one block, no newline anywhere.
  const src = [
    'A man stabbed his estranged wife more than 30 times inside their unit at Block 342B Yishun Ring Road on the night of 13 August 2016.',
    'Mohamad Jonit Adnan, then 37, had visited the flat that evening under the pretence of returning his two daughters after taking them to a nearby playground.',
    'Sri Idayu Ghazali, 29, was found conscious but critically injured when police arrived at about 9pm after her sister raised the alarm.',
    'She was conveyed to Khoo Teck Puat Hospital and pronounced dead at 4.26am the following morning.',
    'Neighbours at the Yishun Ring Road block had reported hearing loud quarrelling from the unit around 8pm, followed by screams, then silence.',
    'He was sentenced by the High Court on 12 July 2018 to 10 years imprisonment.',
  ].join(' ')

  const paras = toParagraphs(src)
  assert.ok(paras.length > 1, 'should produce more than one paragraph')
  // The words must survive the split exactly — this is formatting, not editing.
  assert.equal(paras.join(' ').replace(/\s+/g, ' '), src.replace(/\s+/g, ' '))
})

test('paragraphs: no paragraph is a single stranded short sentence', () => {
  const src = Array.from({ length: 7 }, (_, i) =>
    `Sentence number ${i + 1} runs on for a while so the paragraph budget is exercised properly.`
  ).join(' ')
  for (const p of toParagraphs(src)) {
    assert.ok(p.length >= 60, `stranded fragment: ${p}`)
  }
})

test('paragraphs: empty input yields no paragraphs', () => {
  assert.deepEqual(toParagraphs(''), [])
  assert.deepEqual(toParagraphs(null), [])
  assert.deepEqual(toParagraphs('   \n  '), [])
})

// ── canonicalUrl / uniqueSources ─────────────────────────────────────────────
// One article must never be advertised as two. yishun-python-escapes-drain-
// worksite-aug-2026 shipped as "⚡2 sources" holding one Stomp report twice.
const STOMP = 'https://www.stomp.sg/singapore-seen/workers-yishun-worksite-uncover-slithery-surprise-later-vanishes-drain'

test('canonicalUrl strips tracking params', () => {
  assert.equal(canonicalUrl(STOMP + '?ref=home-editors-picks'), canonicalUrl(STOMP))
  assert.equal(canonicalUrl(STOMP + '?utm_source=fb&utm_campaign=x'), canonicalUrl(STOMP))
})

test('canonicalUrl ignores fragment, trailing slash, www and host case', () => {
  assert.equal(canonicalUrl(STOMP + '#comments'), canonicalUrl(STOMP))
  assert.equal(canonicalUrl(STOMP + '/'), canonicalUrl(STOMP))
  assert.equal(canonicalUrl('https://stomp.sg/a'), canonicalUrl('https://www.stomp.sg/a'))
  assert.equal(canonicalUrl('https://STOMP.sg/a'), canonicalUrl('https://stomp.sg/a'))
})

test('canonicalUrl keeps a query that identifies the article', () => {
  assert.notEqual(canonicalUrl('https://x.sg/read?id=1'), canonicalUrl('https://x.sg/read?id=2'))
  assert.ok(canonicalUrl('https://x.sg/read?id=1&utm_source=fb').endsWith('read?id=1'))
})

test('canonicalUrl does not merge distinct articles or hosts', () => {
  assert.notEqual(canonicalUrl('https://stomp.sg/a'), canonicalUrl('https://stomp.sg/b'))
  assert.notEqual(canonicalUrl('https://stomp.sg/a'), canonicalUrl('https://asiaone.com/a'))
})

test('canonicalUrl degrades on unparseable input', () => {
  assert.equal(canonicalUrl('not a url'), 'not a url')
  assert.equal(canonicalUrl(''), '')
})

test('uniqueSources collapses the production duplicate', () => {
  const got = uniqueSources([STOMP + '?ref=home-editors-picks', STOMP, 'https://asiaone.com/x'])
  assert.equal(got.length, 2)
  assert.equal(got[0], STOMP + '?ref=home-editors-picks')  // first spelling wins
  assert.equal(got[1], 'https://asiaone.com/x')
})

test('uniqueSources tolerates null and empty entries', () => {
  assert.deepEqual(uniqueSources([null, '', undefined, STOMP]), [STOMP])
  assert.deepEqual(uniqueSources(null), [])
  assert.deepEqual(uniqueSources(undefined), [])
})

// ── foreignSourceNote ────────────────────────────────────────────────────────
// Malaysian outlets legitimately corroborate SG incidents; a reader should see
// at a glance that the source sits outside the local press. Operator direction
// 2026-08 named Malay Mail specifically.
test('foreignSourceNote flags Malaysian outlets', () => {
  assert.equal(foreignSourceNote('https://www.malaymail.com/news/singapore/2026/08/01/x/123'),
               '(foreign-linked news source)')
  assert.equal(foreignSourceNote('https://malaymail.com/x'), '(foreign-linked news source)')
  assert.equal(foreignSourceNote('https://www.thestar.com.my/news/x'), '(foreign-linked news source)')
  assert.equal(foreignSourceNote('https://malaysia.news.yahoo.com/x'), '(foreign-linked news source)')
})

test('foreignSourceNote leaves Singapore outlets unmarked', () => {
  assert.equal(foreignSourceNote('https://www.straitstimes.com/singapore/x'), null)
  assert.equal(foreignSourceNote('https://mothership.sg/2026/08/x/'), null)
  assert.equal(foreignSourceNote('https://sg.news.yahoo.com/x'), null)  // SG Yahoo, not MY
  assert.equal(foreignSourceNote('https://www.asiaone.com/singapore/x'), null)
})

test('foreignSourceNote does not match a lookalike domain', () => {
  assert.equal(foreignSourceNote('https://malaymail.com.evil.example/x'), null)
  assert.equal(foreignSourceNote('not a url'), null)
})

// ── spreadOverlappingPins ───────────────────────────────────────────────────
test('spreadOverlappingPins leaves a solitary pin exactly where it is', () => {
  const f = { geometry: { coordinates: [103.8350, 1.4295] as [number, number] } }
  const out = spreadOverlappingPins([f])
  assert.deepStrictEqual(out[0].geometry.coordinates, [103.8350, 1.4295])
})

test('spreadOverlappingPins separates pins sharing one coordinate', () => {
  const at = (id: string) => ({ id, geometry: { coordinates: [103.8350, 1.4295] as [number, number] } })
  const out = spreadOverlappingPins([at('a'), at('b'), at('c')])
  const keys = new Set(out.map(f => f.geometry.coordinates.join(',')))
  assert.strictEqual(keys.size, 3, 'all three must end up distinct/clickable')
  // Every pin stays within the spread radius of its true address.
  for (const f of out) {
    const [lng, lat] = f.geometry.coordinates
    const d = Math.hypot(lng - 103.8350, lat - 1.4295)
    assert.ok(d <= PIN_SPREAD_DEG * 1.0001, `moved ${d} — outside the block footprint`)
  }
  assert.strictEqual(out.length, 3)
})

test('spreadOverlappingPins is deterministic across renders', () => {
  const at = (id: string) => ({ id, geometry: { coordinates: [103.84, 1.43] as [number, number] } })
  const a = spreadOverlappingPins([at('x'), at('y')])
  const b = spreadOverlappingPins([at('x'), at('y')])
  assert.deepStrictEqual(a.map(f => f.geometry.coordinates), b.map(f => f.geometry.coordinates))
})

test('spreadOverlappingPins keeps genuinely distinct coordinates untouched', () => {
  const f1 = { geometry: { coordinates: [103.8350, 1.4295] as [number, number] } }
  const f2 = { geometry: { coordinates: [103.8460, 1.4175] as [number, number] } }
  const out = spreadOverlappingPins([f1, f2])
  assert.deepStrictEqual(out.map(f => f.geometry.coordinates), [f1, f2].map(f => f.geometry.coordinates))
})
