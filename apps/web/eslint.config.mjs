// Flat config — `next lint` was removed in Next.js 16, so ESLint is invoked
// directly (`npm run lint` -> `eslint`).  `eslint-config-next/core-web-vitals`
// already spreads in the base `eslint-config-next` rules and the global ignores
// for `.next/**`, `out/**`, `build/**` and `next-env.d.ts`.
import nextCoreWebVitals from 'eslint-config-next/core-web-vitals'

const config = [
  ...nextCoreWebVitals,
]

export default config
