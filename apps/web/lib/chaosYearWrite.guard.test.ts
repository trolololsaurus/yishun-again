/**
 * Regression guard for the Chaos filter-state URL writer (hooks/useChaosYear.ts).
 *
 * Run:  npm test          (from apps/web)
 *       node --test lib/
 *
 * This is a SOURCE-LEVEL guard, in the same spirit as the War Room's
 * utils.paragraphs.test.ts parity guard: the behaviour it protects (a native
 * <select> driven by useSearchParams) needs a React/Next runtime the repo's
 * node:test + native-TS-stripping harness cannot provide, so instead of
 * exercising the hook we assert the one structural fact the fix depends on.
 *
 * THE BUG THIS GUARDS: onYearChange/onClassChange must update the URL with
 * window.history.replaceState, NOT router.replace(). router.replace() triggers
 * an App Router RSC navigation that must COMMIT before useSearchParams (and thus
 * the <select> value) updates; a pick made before it commits supersedes it, so
 * the control freezes on a year under any sustained interaction — the
 * "year selector gets stuck after a while" bug, reported twice. history.replaceState
 * updates the URL synchronously client-side, with no server round-trip.
 *
 * You cannot call router.replace without useRouter, so forbidding useRouter (which
 * appears nowhere in the file, not even in comments) is a robust proxy for
 * "no router navigation on a filter change".
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

const src = readFileSync(fileURLToPath(new URL('../hooks/useChaosYear.ts', import.meta.url)), 'utf8')

test('useChaosYear writes filter state with history.replaceState (client-side, no RSC nav)', () => {
  assert.match(src, /window\.history\.replaceState\(/, 'must update the URL via window.history.replaceState')
})

test('useChaosYear does not use the router for filter writes (no freeze regression)', () => {
  // No useRouter import or call anywhere — that is what forced an RSC navigation
  // per pick and froze the selector. Its absence is the guard.
  assert.doesNotMatch(src, /useRouter/, 'useRouter must not return: it reintroduces the RSC-navigation freeze')
})
