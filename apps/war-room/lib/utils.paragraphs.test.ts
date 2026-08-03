/**
 * Guard: the War Room's paragraph logic must stay byte-identical to the public
 * site's, because the War Room is the surface where an operator decides whether
 * a summary is fit to publish. If the two diverge, the operator approves one
 * rendering and ships another.
 *
 * There is no packages/shared wired into either app, so the code is duplicated
 * on purpose — and this test is the price of that duplication. It reads both
 * source files and compares the implementations directly, so no amount of
 * "I'll just tweak the War Room copy" can pass silently.
 *
 * Run: node --test apps/war-room/lib/utils.paragraphs.test.ts
 */
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

import { toParagraphs, splitSentences, PARAGRAPH_TARGET } from './utils.ts'

const here = dirname(fileURLToPath(import.meta.url))
const WEB_UTILS    = resolve(here, '../../web/lib/utils.ts')
const WARROOM_UTILS = resolve(here, './utils.ts')

/** Pull the ported block out of a utils.ts, normalised for comparison. */
function extractParagraphBlock(path: string): string {
  const src = readFileSync(path, 'utf8')
  const start = src.indexOf('// Trailing abbreviations that end in')
  assert.notEqual(start, -1, `paragraph block not found in ${path}`)
  const end = src.indexOf('export function chaosDescriptor', start)
  const block = end === -1 ? src.slice(start) : src.slice(start, end)
  return block.replace(/\r\n/g, '\n').trimEnd()
}

test('war-room paragraph logic is identical to the web copy', () => {
  assert.equal(
    extractParagraphBlock(WARROOM_UTILS),
    extractParagraphBlock(WEB_UTILS),
    'apps/war-room/lib/utils.ts and apps/web/lib/utils.ts have diverged — ' +
    'change both or neither.',
  )
})

test('author-supplied blank lines are honoured', () => {
  assert.deepEqual(toParagraphs('First para.\n\nSecond para.'), ['First para.', 'Second para.'])
})

test('empty and blank input yields no paragraphs', () => {
  assert.deepEqual(toParagraphs(''), [])
  assert.deepEqual(toParagraphs(null), [])
  assert.deepEqual(toParagraphs('   \n  '), [])
})

test('a short summary stays a single paragraph', () => {
  const s = 'A man was arrested in Yishun. Police are investigating.'
  assert.deepEqual(toParagraphs(s), [s])
})

test('an unbroken wall of text is split on sentence boundaries', () => {
  const sentence = 'Police responded to the incident at the block that evening. '
  const src = sentence.repeat(12).trim()
  const paras = toParagraphs(src)
  assert.ok(paras.length > 1, 'expected the blob to be broken up')
  // No word may be added, removed or reordered — only breaks inserted.
  assert.equal(paras.join(' ').replace(/\s+/g, ' '), src.replace(/\s+/g, ' '))
})

test('abbreviations do not end a sentence', () => {
  assert.deepEqual(splitSentences('Mr. Tan called the police. They arrived.'),
                   ['Mr. Tan called the police.', 'They arrived.'])
})

test('PARAGRAPH_TARGET is shared, not re-guessed', () => {
  assert.equal(PARAGRAPH_TARGET, 320)
})
