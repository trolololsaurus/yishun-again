import { NextResponse } from 'next/server'
import { supabase } from '@/lib/supabase'

export async function GET() {
  const { data, error } = await supabase
    .from('war_room_queue')
    .select('*')
    .in('status', ['pending', 'update'])
    .order('created_at', { ascending: false })

  if (error) {
    console.error('GET /api/queue:', error)
    return NextResponse.json({ error: error.message }, { status: 500 })
  }

  return NextResponse.json(data)
}
