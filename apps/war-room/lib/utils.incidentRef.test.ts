/**
 * Guard: /rectify's "find one incident" box.
 *
 * The operator pastes whatever they were looking at when they decided a picture
 * was wrong — the public page, the War Room preview, or the source article. All
 * three have to land on the same row, and a bare slug must never be mistaken
 * for a hostname (`new URL('https://yishun-cat-abuse-feb-2017')` parses fine).
 *
 * Run: node --test apps/war-room/lib/utils.incidentRef.test.ts
 */
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { incidentRefFromInput, sanitiseSlug, canonicalUrl } from './utils.ts'

test('the public incident URL resolves to its slug', () => {
  assert.deepEqual(
    incidentRefFromInput('https://www.yishunagain.com/incidents/yishun-mcdonalds-bomb-hoax-2023'),
    { slug: 'yishun-mcdonalds-bomb-hoax-2023', sourceUrl: null },
  )
})

test('the War Room preview URL resolves to the same slug', () => {
  assert.equal(
    incidentRefFromInput('https://warroom.yishunagain.com/incidents/yishun-hailstones-2018').slug,
    'yishun-hailstones-2018',
  )
  assert.equal(
    incidentRefFromInput('http://localhost:3001/incidents/yishun-hailstones-2018').slug,
    'yishun-hailstones-2018',
  )
})

test('trailing slash, query and fragment are ignored', () => {
  for (const u of [
    'https://www.yishunagain.com/incidents/yishun-hailstones-2018/',
    'https://www.yishunagain.com/incidents/yishun-hailstones-2018?utm_source=share',
    'https://www.yishunagain.com/incidents/yishun-hailstones-2018#sources',
  ]) {
    assert.equal(incidentRefFromInput(u).slug, 'yishun-hailstones-2018', u)
  }
})

test('a scheme-less site URL still works', () => {
  assert.equal(
    incidentRefFromInput('www.yishunagain.com/incidents/yishun-hailstones-2018').slug,
    'yishun-hailstones-2018',
  )
})

test('a bare slug is a slug, not a hostname', () => {
  // The whole reason _LOOKS_LIKE_URL exists: 'https://' + this parses as a host.
  assert.deepEqual(
    incidentRefFromInput('yishun-cat-abuse-feb-2017'),
    { slug: 'yishun-cat-abuse-feb-2017', sourceUrl: null },
  )
})

test('a source article URL is returned as a source, not a slug', () => {
  const ref = incidentRefFromInput(
    'https://www.straitstimes.com/singapore/6-to-be-charged-with-rioting-after-yishun-fight-that-left-man-unconscious')
  assert.equal(ref.slug, null)
  assert.match(ref.sourceUrl ?? '', /straitstimes\.com/)
})

test('a source URL matches its stored spelling after canonicalisation', () => {
  // Stomp URLs are stored with the ref parameter the scraper found them with;
  // the operator copies the clean one out of the address bar.
  const pasted = incidentRefFromInput('https://www.stomp.sg/trending-now/6-be-charged-rioting-after-yishun-fight-left-man-unconscious')
  const stored = 'https://www.stomp.sg/trending-now/6-be-charged-rioting-after-yishun-fight-left-man-unconscious?ref=home-trending-now'
  assert.equal(canonicalUrl(pasted.sourceUrl ?? ''), canonicalUrl(stored))
})

test('empty input resolves to nothing', () => {
  for (const v of ['', '   ', null, undefined]) {
    assert.deepEqual(incidentRefFromInput(v), { slug: null, sourceUrl: null })
  }
})

test('slug sanitising strips anything Supabase should not see', () => {
  assert.equal(sanitiseSlug('Yishun-Hailstones-2018'), 'yishun-hailstones-2018')
  assert.equal(sanitiseSlug("a'; drop table incidents;--"), 'adroptableincidents--')
})
