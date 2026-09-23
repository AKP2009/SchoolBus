import type { Config } from 'tailwindcss';

// Tokens only (docs/design.md). `colors` replaces Tailwind's palette on purpose so an ad-hoc
// `bg-red-500` can't compile into the product.
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    colors: {
      transparent: 'transparent',
      current: 'currentColor',
      inherit: 'inherit',
      saffron: { 400: 'var(--saffron-400)', 500: 'var(--saffron-500)', 600: 'var(--saffron-600)' },
      'on-saffron': 'var(--on-saffron)',
      'on-status': 'var(--on-status)',
      ok: 'var(--ok)',
      info: 'var(--info)',
      warning: 'var(--warning)',
      critical: 'var(--critical)',
      offline: 'var(--offline)',
      tint: {
        ok: 'var(--tint-ok)',
        info: 'var(--tint-info)',
        warning: 'var(--tint-warning)',
        critical: 'var(--tint-critical)',
        offline: 'var(--tint-offline)',
      },
      bg: 'var(--bg)',
      surface: 'var(--surface)',
      raised: 'var(--raised)',
      line: 'var(--border)',
      band: 'var(--band)',
      ink: { DEFAULT: 'var(--text)', 2: 'var(--text-2)', 3: 'var(--text-3)' },
      header: { DEFAULT: 'var(--header)', ink: 'var(--on-header)', 'ink-2': 'var(--on-header-2)' },
      series: {
        1: 'var(--series-1)', 2: 'var(--series-2)', 3: 'var(--series-3)',
        4: 'var(--series-4)', 5: 'var(--series-5)', 6: 'var(--series-6)',
      },
    },
    extend: {
      fontFamily: { sans: ['var(--font-sans)'], mono: ['var(--font-mono)'] },
      borderRadius: { sm: 'var(--radius-sm)', md: 'var(--radius-md)', lg: 'var(--radius-lg)' },
      borderColor: { DEFAULT: 'var(--border)' },
      boxShadow: { float: 'var(--shadow-float)' },
      fontSize: {
        'cab-display': ['56px', { lineHeight: '60px', fontWeight: '700' }],
        'cab-h1': ['32px', { lineHeight: '38px', fontWeight: '700' }],
        'cab-h2': ['24px', { lineHeight: '30px', fontWeight: '500' }],
        'cab-body': ['20px', { lineHeight: '28px', fontWeight: '400' }],
        'cab-small': ['18px', { lineHeight: '24px', fontWeight: '400' }],
        'office-h1': ['28px', { lineHeight: '34px', fontWeight: '700' }],
        'office-h2': ['20px', { lineHeight: '26px', fontWeight: '500' }],
        'office-h3': ['16px', { lineHeight: '22px', fontWeight: '700' }],
        'office-body': ['15px', { lineHeight: '22px', fontWeight: '400' }],
        'office-small': ['13px', { lineHeight: '18px', fontWeight: '400' }],
        'office-kpi': ['36px', { lineHeight: '40px', fontWeight: '500' }],
      },
      minHeight: { 'touch-cab': '64px', 'touch-office': '40px' },
      minWidth: { 'touch-cab': '64px', 'touch-office': '40px' },
      height: { 'status-bar': '56px', 'bottom-bar': '88px' },
    },
  },
  plugins: [],
} satisfies Config;
