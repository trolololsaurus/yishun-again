import { NextResponse } from 'next/server'
import { supabase } from '@/lib/supabase'

export async function GET() {
  const { data, error } = await supabase
    .from('sources')
    .select('*')
    .order('approved_by_operator', { ascending: true })   // unapproved first
    .order('name')

  if (error) {
    console.error('GET /api/sources:', error)
    return NextResponse.json({ error: error.message }, { status: 500 })
  }

  return NextResponse.json(data)
}
