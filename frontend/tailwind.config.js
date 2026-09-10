/** Tokens live in src/index.css as RGB channel triplets; this file only gives
 *  them Tailwind names. The <alpha-value> placeholder is what keeps opacity
 *  modifiers (bg-status-critical/10, border-accent/40) working against a
 *  custom property. */
const token = (name) => `rgb(var(--${name}) / <alpha-value>)`

/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        surface: token('surface'),
        panel: token('panel'),
        raised: token('raised'),
        edge: { DEFAULT: token('edge'), strong: token('edge-strong') },
        ink: {
          DEFAULT: token('ink'),
          muted: token('ink-muted'),
          faint: token('ink-faint'),
        },
        // Fixed attention ramp. `critical` is the mark step (rails, fills,
        // borders — 3:1 territory); `critical-ink` is the same meaning at text
        // sizes, where 4.5:1 applies.
        status: {
          good: token('status-good'),
          warning: token('status-warning'),
          serious: token('status-serious'),
          critical: token('status-critical'),
          'critical-ink': token('status-critical-ink'),
        },
        accent: { DEFAULT: token('accent'), ink: token('accent-ink') },
      },
      fontFamily: {
        sans: ['Inter Variable', 'Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono Variable', 'JetBrains Mono', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      // Seven roles, fixed. Body sits at 13px — denser than Tailwind's 14px
      // default and the right weight for tabular reading.
      fontSize: {
        micro: ['11px', { lineHeight: '14px', letterSpacing: '0.08em' }],
        label: ['12px', { lineHeight: '16px' }],
        body: ['13px', { lineHeight: '18px' }],
        heading: ['14px', { lineHeight: '20px' }],
        title: ['18px', { lineHeight: '24px', letterSpacing: '-0.01em' }],
        display: ['24px', { lineHeight: '28px', letterSpacing: '-0.02em' }],
      },
      transitionDuration: { DEFAULT: '120ms' },
      transitionTimingFunction: { DEFAULT: 'cubic-bezier(0, 0, 0.2, 1)' },
    },
  },
  plugins: [],
}
