/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      fontFamily: {
        sans: ["DM Sans", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "monospace"],
      },
      colors: {
        surface: {
          DEFAULT: "rgb(var(--surface) / <alpha-value>)",
          elevated: "rgb(var(--surface-elevated) / <alpha-value>)",
        },
        ink: {
          DEFAULT: "rgb(var(--ink) / <alpha-value>)",
          muted: "rgb(var(--ink-muted) / <alpha-value>)",
        },
        accent: {
          DEFAULT: "rgb(var(--accent) / <alpha-value>)",
          dim: "rgb(var(--accent-dim) / <alpha-value>)",
        },
        line: "rgb(var(--line) / <alpha-value>)",
      },
      boxShadow: {
        panel: "0 4px 24px -4px rgb(0 0 0 / 0.12), 0 8px 48px -8px rgb(0 0 0 / 0.08)",
        "panel-dark":
          "0 4px 24px -4px rgb(0 0 0 / 0.45), 0 8px 48px -8px rgb(0 0 0 / 0.35)",
      },
    },
  },
  plugins: [],
};
