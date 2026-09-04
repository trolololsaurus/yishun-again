/**
 * Guard: applying an update and undoing it round-trips exactly.
 *
 * A confirmed update mutates a LIVE incident (appends a source + timeline entry,
 * bumps update_count, recomputes dates, optionally rewrites the summary). The
 * undo restores a snapshot. If revert(apply(x)) != x the operator's "undo" would
 * leave the incident in some third state — worse than the wrong merge it meant
 * to fix. These assert the pair is a clean inverse over the merge-relevant fields,
 * including the two edge cases that make surgical un-append wrong.
 *
 * Run: node --test apps/war-room/lib/utils.updateMerge.test.ts
 */
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { applyUpdate, revertUpdate, applySignalCorroboration, type IncidentMergeState } from './utils.ts'

// The fields applyUpdate/revertUpdate touch — what "== x" is measured over.
const FIELDS = [
  'source_urls', 'source_timeline', 'update_count',
  'incident_date', 'first_reported_at', 'is_developing', 'summary',
  'edmw_signal_count',
] as const

function pick(state: Record<string, unknown>) {
  return Object.fromEntries(FIELDS.map(f => [f, state[f]]))
}

function roundTrip(existing: IncidentMergeState, input: Parameters<typeof applyUpdate>[1]) {
  const before = pick(existing as unknown as Record<string, unknown>)
  const { updates, snapshot } = applyUpdate(existing, input)
  const after = { ...existing, ...updates }
  // Sanity: the merge actually changed something.
  assert.notDeepEqual(pick(after), before, 'apply should mutate the incident')
  const restored = { ...after, ...revertUpdate(snapshot) }
  assert.deepEqual(pick(restored), before, 'revert(apply(x)) must equal x')
}

const base: IncidentMergeState = {
  source_urls:       ['https://www.straitstimes.com/a'],
  source_timeline:   [{ date: '2026-08-01', source_url: 'https://www.straitstimes.com/a', role: 'initial' }],
  update_count:      0,
  incident_date:     '2026-08-01',
  first_reported_at: '2026-08-01',
  is_developing:     false,
  summary:           'Original summary.',
  edmw_signal_count: 0,
}

test('a plain merge of a newer source round-trips', () => {
  roundTrip(base, {
    newSourceUrl: 'https://www.channelnewsasia.com/b',
    sourceName:   'CNA',
    headline:     'Follow-up: man charged',
    newDate:      '2026-08-10',
  })
})

test('an operator summary edit round-trips (summary restored)', () => {
  roundTrip(base, {
    newSourceUrl: 'https://www.channelnewsasia.com/b',
    sourceName:   'CNA',
    headline:     'Follow-up',
    newDate:      '2026-08-10',
    updatedSummary: 'Rewritten merged summary that is different.',
  })
})

test('a source already present does NOT get removed on undo', () => {
  // apply is a no-op on source_urls here; a surgical un-append would delete the
  // citation the incident already had. Snapshot-restore keeps it.
  const existing = { ...base, source_urls: ['https://www.straitstimes.com/a', 'https://www.channelnewsasia.com/b'] }
  const { updates, snapshot } = applyUpdate(existing, {
    newSourceUrl: 'https://www.channelnewsasia.com/b',
    sourceName:   'CNA',
    headline:     'dup',
    newDate:      '2026-08-05',
  })
  const after = { ...existing, ...updates }
  assert.deepEqual((after as IncidentMergeState).source_urls, existing.source_urls, 'no duplicate appended')
  const restored = { ...after, ...revertUpdate(snapshot) }
  assert.deepEqual(pick(restored), pick(existing as unknown as Record<string, unknown>))
})

test('an older source does not push incident_date backwards, and still undoes', () => {
  roundTrip(base, {
    newSourceUrl: 'https://www.channelnewsasia.com/old',
    sourceName:   'CNA',
    headline:     'earlier report surfaced',
    newDate:      '2026-07-20', // earlier than incident_date
  })
  // incident_date stays the later date. first_reported_at is UNCHANGED: the
  // ported route only adopts newDate when there was no existing first date at
  // all, so an already-set first_reported_at is never moved. (Pre-existing
  // behaviour, faithfully preserved — not fixed here.)
  const { updates } = applyUpdate(base, {
    newSourceUrl: 'https://www.channelnewsasia.com/old', sourceName: 'CNA',
    headline: 'x', newDate: '2026-07-20',
  })
  assert.equal(updates.incident_date, '2026-08-01')
  assert.equal(updates.first_reported_at, '2026-08-01')
})

test('a dateless candidate never corrupts the dates, and still undoes', () => {
  roundTrip(base, {
    newSourceUrl: 'https://www.channelnewsasia.com/undated',
    sourceName:   'CNA',
    headline:     'no date',
    newDate:      null,
  })
})

// ── Signal (forum/UGC) corroboration — guardrail #2's non-citation path ──────
//
// A signal source (Reddit/EDMW) confirming an update must NEVER touch
// source_urls/source_timeline (that would be a citation) — it only bumps
// edmw_signal_count, the same "Forum buzz" counter a signal-only match on a
// brand-new incident gets. applySignalCorroboration shares revertUpdate with
// applyUpdate, so the round-trip contract must hold here too.

test('signal corroboration bumps edmw_signal_count and round-trips, never touching source_urls', () => {
  const before = pick(base as unknown as Record<string, unknown>)
  const { updates, snapshot } = applySignalCorroboration(base, {})
  assert.equal(updates.source_urls, undefined, 'signal path must not set source_urls')
  assert.equal(updates.source_timeline, undefined, 'signal path must not set source_timeline')
  const after = { ...base, ...updates }
  assert.equal(after.edmw_signal_count, 1, 'count bumped by exactly one')
  assert.notDeepEqual(pick(after), before, 'apply should mutate the incident')
  const restored = { ...after, ...revertUpdate(snapshot) }
  assert.deepEqual(pick(restored), before, 'revert(applySignalCorroboration(x)) must equal x')
})

test('signal corroboration with an operator-edited summary round-trips', () => {
  const before = pick(base as unknown as Record<string, unknown>)
  const { updates, snapshot } = applySignalCorroboration(base, { updatedSummary: 'Edited on signal confirm.' })
  const after = { ...base, ...updates }
  assert.equal(after.summary, 'Edited on signal confirm.')
  const restored = { ...after, ...revertUpdate(snapshot) }
  assert.deepEqual(pick(restored), before)
})

test('a second signal corroboration on an already-corroborated incident accumulates', () => {
  const oncePrepped = { ...base, edmw_signal_count: 3 }
  const { updates, snapshot } = applySignalCorroboration(oncePrepped, {})
  assert.equal(updates.edmw_signal_count, 4)
  const after = { ...oncePrepped, ...updates }
  const restored = { ...after, ...revertUpdate(snapshot) }
  assert.equal(restored.edmw_signal_count, 3, 'undo returns to the pre-corroboration count, not 0')
})
