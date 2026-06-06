/**
 * Year rollover utilities for the Chaos Panel.
 *
 * Stats are always computed from the incidents table filtered by year, so no
 * data migration is needed at rollover — the new year simply starts at zero
 * until incidents are logged. These helpers let callers detect the rollover
 * window and read the canonical default year.
 */

/** Returns the current calendar year in SGT (UTC+8). */
export function getCurrentDefaultYear(): number {
  const nowSGT = new Date(Date.now() + 8 * 60 * 60 * 1000)
  return nowSGT.getUTCFullYear()
}

/**
 * Returns true during the 5-minute window after SGT midnight on Jan 1.
 * Useful for triggering a cache revalidation or a "Happy New Year" banner.
 *
 * Window: Jan 1 00:00–00:05 SGT (= Dec 31 16:00–16:05 UTC)
 */
export function shouldRollover(): boolean {
  const nowSGT = new Date(Date.now() + 8 * 60 * 60 * 1000)
  return (
    nowSGT.getUTCMonth()   === 0 &&   // January
    nowSGT.getUTCDate()    === 1 &&   // 1st
    nowSGT.getUTCHours()   === 0 &&   // midnight hour
    nowSGT.getUTCMinutes() < 5        // first 5 minutes
  )
}
