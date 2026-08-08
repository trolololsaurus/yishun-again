// Run: node --test apps/web/lib/params.test.ts
// Same runner as utils.test.ts — node:test + Node's native TS stripping, which
// is why the import of ./params.ts carries the extension. params.ts is
// dependency-free, so nothing here needs the Next resolver.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { parseYear, patchedParams, buildHref } from './params.ts'

// Helper: build a params object from a query string.
const sp = (q: string) => new URLSearchParams(q)

test('parseYear: well-formed 4-digit year', () => {
  assert.equal(parseYear(sp('year=2024')), 2024)
  assert.equal(parseYear(sp('year=2026&class=dagger')), 2026)
})

test('parseYear: missing / malformed → null', () => {
  assert.equal(parseYear(sp('')), null)
  assert.equal(parseYear(sp('year=')), null)
  assert.equal(parseYear(sp('year=abcd')), null)
  assert.equal(parseYear(sp('year=24')), null)      // too short
  assert.equal(parseYear(sp('year=20260')), null)   // too long
  assert.equal(parseYear(sp('year=-202')), null)
})

test('patchedParams: sets a new key', () => {
  assert.equal(patchedParams(sp(''), { year: '2024' }).toString(), 'year=2024')
})

test('patchedParams: null or empty deletes the key', () => {
  assert.equal(patchedParams(sp('year=2024'), { year: null }).toString(), '')
  assert.equal(patchedParams(sp('year=2024'), { year: '' }).toString(), '')
})

test('patchedParams: preserves other keys, does not mutate input', () => {
  const input = sp('class=dagger&year=2024')
  const out   = patchedParams(input, { year: '2025' })
  assert.equal(out.get('class'), 'dagger')
  assert.equal(out.get('year'), '2025')
  // input untouched
  assert.equal(input.get('year'), '2024')
})

test('buildHref: appends query only when present', () => {
  assert.equal(buildHref('/map', sp('')), '/map')
  assert.equal(buildHref('/map', sp('year=2024')), '/map?year=2024')
  assert.equal(buildHref('/', patchedParams(sp('year=2026'), { year: null })), '/')
})
