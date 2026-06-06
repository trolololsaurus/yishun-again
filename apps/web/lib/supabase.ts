import { createClient } from '@supabase/supabase-js'

// Server-side only — uses publishable key for public reads.
// Falls back to placeholder values so Next.js can complete the build without
// env vars set; actual queries will fail at request time if unconfigured.
export const supabase = createClient(
  process.env.SUPABASE_URL                        ?? 'http://localhost',
  process.env.SUPABASE_PUBLISHABLE_KEY ?? 'placeholder',
  { auth: { persistSession: false } }
)
