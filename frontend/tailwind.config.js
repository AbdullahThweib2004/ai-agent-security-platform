/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        // Console surfaces (dark by design — this is an operator tool).
        surface: '#12151c',
        panel: '#181d27',
        raised: '#1f2532',
        edge: '#2b3446',
        ink: {
          DEFAULT: '#e6e9ef',
          muted: '#98a2b8',
          faint: '#6b7488',
        },
        // Status palette: fixed, never themed, never reused as a series color.
        // Always paired with an icon + label so hue is never the only signal.
        status: {
          good: '#0ca30c',
          warning: '#fab219',
          serious: '#ec835a',
          critical: '#d03b3b',
        },
        accent: '#3987e5',
      },
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
    },
  },
  plugins: [],
}
