import type { Config } from 'tailwindcss'

/**
 * The palette is fixed. Every colour here maps to a clinical meaning, and a
 * value that does not carry one is not in the theme.
 *
 * risk (#E05C2A) is the only accent in the product. It marks the osteoporosis
 * threshold, an elevated risk score, and the primary action. Using it anywhere
 * decorative would spend the one signal the interface has.
 */
const config: Config = {
  content: ['./app/**/*.{ts,tsx}', './components/**/*.{ts,tsx}', './lib/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        base: '#0D1117',
        surface: '#161B22',
        'surface-raised': '#1C232C',
        line: '#2A313C',
        'line-strong': '#3A424E',

        ink: '#E6EDF3',
        'ink-muted': '#8B949E',
        'ink-faint': '#6E7681',

        // Clinical bands. Identical names are used on the chart, the badges,
        // and the scan list so one T-score never renders two different colours.
        safe: '#238636',
        warning: '#D29922',
        danger: '#E05C2A',
        risk: '#E05C2A',
      },
      fontFamily: {
        sans: ['var(--font-inter)', 'system-ui', 'sans-serif'],
        mono: ['var(--font-dm-mono)', 'ui-monospace', 'monospace'],
      },
      fontSize: {
        // Tight tracking on large numerals. A 10 year probability rendered at
        // display size needs to read as one value, not four digits.
        metric: ['3.25rem', { lineHeight: '1', letterSpacing: '-0.03em' }],
        'metric-sm': ['2rem', { lineHeight: '1.05', letterSpacing: '-0.02em' }],
      },
      borderRadius: {
        // Nothing rounder than this anywhere. Instrument panels have corners.
        none: '0',
        sm: '2px',
        DEFAULT: '2px',
        md: '3px',
      },
    },
  },
  plugins: [],
}

export default config
