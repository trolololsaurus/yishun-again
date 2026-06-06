import { createClient } from '@supabase/supabase-js'

const url = process.env.SUPABASE_URL
const secret = process.env.SUPABASE_SECRET_KEY

if (!url || !secret) {
  throw new Error('SUPABASE_URL and SUPABASE_SECRET_KEY must be set')
}

// Admin client — bypasses RLS. Server-side only. Never expose to browser.
export const supabase = createClient(url, secret, {
  auth: { persistSession: false },
})
