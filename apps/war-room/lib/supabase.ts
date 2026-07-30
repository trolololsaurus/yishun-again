import { createClient } from '@supabase/supabase-js'

// Hard stop if this module is ever pulled into a client bundle — it wields
// the RLS-bypassing secret key. (The env vars are non-NEXT_PUBLIC so the key
// itself wouldn't be inlined, but fail loudly rather than mysteriously.)
if (typeof window !== 'undefined') {
  throw new Error('lib/supabase.ts is server-only — never import it from a client component')
}

const url = process.env.SUPABASE_URL
const secret = process.env.SUPABASE_SECRET_KEY

if (!url || !secret) {
  throw new Error('SUPABASE_URL and SUPABASE_SECRET_KEY must be set')
}

// Admin client — bypasses RLS. Server-side only. Never expose to browser.
export const supabase = createClient(url, secret, {
  auth: { persistSession: false },
})
