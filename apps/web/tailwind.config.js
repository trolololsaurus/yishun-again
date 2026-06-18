/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './app/**/*.{ts,tsx}',
    './components/**/*.{ts,tsx}',
    './lib/**/*.{ts,tsx}',
  ],
  theme: {
    extend: {
      colors: {
        // ── Panzer Dragoon Saga — Midnight Command Centre ───────────
        bg:          '#0A0E1A',  // deep midnight blue-black
        surface:     '#0F1526',  // dark navy — cards, panels
        surface2:    '#0F1A2E',  // hover surface (slightly lighter, blue tint)
        border:      '#1E2D4A',  // steel blue — borders, dividers
        border2:     '#1E2D4A',
        amber:       '#C07830',  // amber — primary accent, logo, scores, links
        'amber-lt':  '#C07830',  // (no separate light amber in PDS palette)
        'amber-dim': '#805828',  // dimmed amber — /100, metadata
        sienna:      '#803018',  // active states, hover backgrounds
        parchment:   '#E8E8F0',  // text-primary
        muted:       '#7A8BAA',  // text-secondary
        dim:         '#3D4F6A',  // text-dim
        ink:         '#0A0E1A',
        'map-bg':    '#070B14',  // near-black blue — map placeholder
        // ── Semantic aliases (kept so existing class names resolve) ──
        'text-primary':   '#E8E8F0',  // near-white with blue tint
        'text-secondary': '#7A8BAA',  // steel blue-grey — meta / dates
        'text-dim':       '#3D4F6A',  // dim steel — disabled
        yellow:  '#C07830',  // interactive accent (amber)
        red:     '#FF6B6B',  // coral red (dark events)
        purple:  '#FF6B6B',  // coral red (dark events)
        green:   '#4ECDC4',  // teal-cyan (good vibes)
        forest:  '#4ECDC4',  // teal-cyan (good vibes / heart)
        // ── Classification colours ──────────────────────────────────
        heart:   '#4ECDC4',  // teal-cyan — GOOD VIBES
        clown:   '#FFE66D',  // bright yellow — ABSURDITIES
        dagger:  '#FF6B6B',  // coral red — DARK EVENTS
        'good-vibes':  '#4ECDC4',
        'absurdities': '#FFE66D',
        'dark-events': '#FF6B6B',
        // CULTURE — custom_label='CULTURE' (pop-culture / media mentions)
        culture: '#A78BFA',
        // map pin colours
        'pin-heart':   '#4ECDC4',
        'pin-clown':   '#FFE66D',
        'pin-dagger':  '#FF6B6B',
        'pin-culture': '#A78BFA',
      },
      fontFamily: {
        display: ['"Press Start 2P"', 'monospace'],
        body:    ['"Courier Prime"', '"Courier New"', 'monospace'],
      },
      screens: {
        sm:  '320px',
        md:  '768px',
        lg:  '1024px',
      },
    },
  },
  plugins: [],
}
