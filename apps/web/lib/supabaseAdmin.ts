import { createClient } from '@supabase/supabase-js'

// Server-only, secret-key client. Deliberately separate from lib/supabase.ts
// (which uses the publishable key everywhere else in this app) — this exists
// for exactly one write: the page_events dwell-time UPDATE in
// /api/track/pageview, after the anon publishable key was confirmed (via live
// testing, 2026-08-26) to silently no-op UPDATEs through Supabase's key
// gateway even with correct Postgres GRANTs and RLS policies in place. See
// the comment in 019_page_events.sql. The route itself is the trust boundary
// here (rate-limited, validates the row id, clamps dwell_ms) — this bypasses
// RLS by design, the same way war-room and the agents backend already do.
if (typeof window !== 'undefined') {
  throw new Error('lib/supabaseAdmin.ts is server-only — never import it from a client component')
}

const url    = process.env.SUPABASE_URL
const secret = process.env.SUPABASE_SECRET_KEY

export const supabaseAdmin = url && secret
  ? createClient(url, secret, { auth: { persistSession: false } })
  : null
