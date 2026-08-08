// Run: node --test apps/web/lib/teaser.test.ts (also picked up by `npm test`).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mapTeaser } from './teaser.ts'

test('mapTeaser: empty / null / undefined → ""', () => {
  assert.equal(mapTeaser(null), '')
  assert.equal(mapTeaser(undefined), '')
  assert.equal(mapTeaser(''), '')
  assert.equal(mapTeaser('   '), '')
})

test('mapTeaser: short summary passes through, whitespace collapsed', () => {
  assert.equal(mapTeaser('A short one.'), 'A short one.')
  assert.equal(mapTeaser('line one\n\nline two'), 'line one line two')
  assert.equal(mapTeaser('  padded \t text  '), 'padded text')
})

test('mapTeaser: long summary truncates at a word boundary with an ellipsis', () => {
  const s = 'word '.repeat(60).trim()   // 300 chars, all spaces between words
  const out = mapTeaser(s)
  assert.ok(out.length <= 121, `expected <=121, got ${out.length}`)
  assert.ok(out.endsWith('…'))
  assert.ok(!out.includes('  '))
  // never ends mid-word: the char before the ellipsis is a full word char
  assert.ok(/word…$/.test(out))
})

test('mapTeaser: a long unbroken token hard-cuts rather than returning almost nothing', () => {
  const out = mapTeaser('x'.repeat(300))
  assert.equal(out, 'x'.repeat(120) + '…')
})
