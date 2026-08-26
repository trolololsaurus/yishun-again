'use client'

const SESSION_KEY = 'ya_session_id'

// Shared by PageViewTracker and ShareButton so a share click lands in the
// same session as the pageview it happened on.
export function getSessionId(): string {
  try {
    let id = sessionStorage.getItem(SESSION_KEY)
    if (!id) {
      id = crypto.randomUUID()
      sessionStorage.setItem(SESSION_KEY, id)
    }
    return id
  } catch {
    // Private-mode / storage blocked — degrade to a one-off id rather than
    // throw; every event from this tab just looks like its own session.
    return crypto.randomUUID()
  }
}

// Fire-and-forget POST to the tracking endpoint. sendBeacon over fetch: it
// survives page unload, which matters for both the create and dwell-fill
// beacons — see PageViewTracker.tsx for why ordering between them matters.
// Returns whether the browser accepted the beacon (false if sendBeacon is
// unavailable or the payload was rejected), so callers can fall back to fetch.
export function sendTrackingBeacon(payload: object): boolean {
  return navigator.sendBeacon?.(
    '/api/track/pageview',
    new Blob([JSON.stringify(payload)], { type: 'application/json' }),
  ) ?? false
}
