/**
 * URL search-param helpers — the single source of truth for HUD filter state.
 *
 * `?year=` (and, from Phase 6, `?class=`) persists across the /↔/map navigation
 * because the Nav links and the sidebar controls all round-trip through here.
 *
 * Deliberately (almost) dependency-free: these are pure functions over a minimal
 * `{ get, toString }` shape, which BOTH `URLSearchParams` and Next's
 * `ReadonlyURLSearchParams` satisfy. The only import is `type FilterState`,
 * which Node's TS stripping erases at runtime — so the module still runs under
 * raw Node for `params.test.ts` (no framework). Importing a *value* from
 * `./utils` would need a `.ts` extension the Next build rejects, so the 4-digit
 * year check is inlined rather than pulled from `sanitiseYear`.
 */

import type { FilterState } from './types'

// Structural type satisfied by URLSearchParams and ReadonlyURLSearchParams alike.
type ReadonlyParams = { get(name: string): string | null; toString(): string }

/** The `?year=` value if it is a well-formed 4-digit year, else null. */
export function parseYear(params: ReadonlyParams): number | null {
  const raw = params.get('year')
  return /^\d{4}$/.test(raw ?? '') ? parseInt(raw as string, 10) : null
}

/** The `?class=` filter, defaulting to 'all' for absent or unknown values. */
export function parseClass(params: ReadonlyParams): FilterState {
  const raw = params.get('class')
  return raw === 'heart' || raw === 'clown' || raw === 'dagger' ? raw : 'all'
}

/**
 * Clone `params` with `patch` applied. A null or empty-string value DELETES the
 * key (so the default year drops out and the URL stays clean at `/`), any other
 * value sets it. Order of the surviving keys is preserved.
 */
export function patchedParams(
  params: ReadonlyParams,
  patch: Record<string, string | null>,
): URLSearchParams {
  const next = new URLSearchParams(params.toString())
  for (const [k, v] of Object.entries(patch)) {
    if (v === null || v === '') next.delete(k)
    else next.set(k, v)
  }
  return next
}

/** `path` with the current query string appended (nothing appended when empty). */
export function buildHref(path: string, params: ReadonlyParams): string {
  const qs = params.toString()
  return qs ? `${path}?${qs}` : path
}
