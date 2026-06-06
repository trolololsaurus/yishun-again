/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './app/**/*.{ts,tsx}',
    './components/**/*.{ts,tsx}',
  ],
  theme: {
    extend: {
      colors: {
        bg:        '#0D0D0D',
        surface:   '#1A1A1A',
        border:    '#333333',
        red:       '#E74C3C',
        yellow:    '#F1C40F',
        purple:    '#8E44AD',
        'text-primary':   '#F5F5F5',
        'text-secondary': '#AAAAAA',
        heart:   '#E74C3C',
        clown:   '#F1C40F',
        dagger:  '#8E44AD',
        green:   '#27AE60',
      },
      fontFamily: {
        display: ['"Press Start 2P"', 'monospace'],
        body:    ['"Courier Prime"', '"Courier New"', 'monospace'],
      },
    },
  },
  plugins: [],
}
