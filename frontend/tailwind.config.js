const v = (name) => `rgb(var(--${name}) / <alpha-value>)`;
const scale = (name, steps) => Object.fromEntries(steps.map((s) => [s, v(`${name}-${s}`)]));

/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: ["selector", '[data-theme="dark"]'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', '-apple-system', 'Segoe UI', 'Roboto', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'Consolas', 'monospace'],
      },
      boxShadow: {
        card: "0 1px 2px rgb(var(--shadow) / 0.06), 0 1px 3px rgb(var(--shadow) / 0.05)",
        lift: "0 8px 24px -6px rgb(var(--shadow) / 0.16), 0 2px 6px rgb(var(--shadow) / 0.06)",
        pop: "0 20px 48px -12px rgb(var(--shadow) / 0.35)",
      },
      keyframes: {
        "fade-in": { from: { opacity: "0" }, to: { opacity: "1" } },
        "slide-up": { from: { opacity: "0", transform: "translateY(8px)" }, to: { opacity: "1", transform: "translateY(0)" } },
        "scale-in": { from: { opacity: "0", transform: "scale(0.97)" }, to: { opacity: "1", transform: "scale(1)" } },
        shimmer: { "100%": { transform: "translateX(100%)" } },
        dot: { "0%, 80%, 100%": { opacity: "0.25", transform: "translateY(0)" }, "40%": { opacity: "1", transform: "translateY(-3px)" } },
        "toast-in": { from: { opacity: "0", transform: "translateY(12px) scale(0.98)" }, to: { opacity: "1", transform: "none" } },
      },
      animation: {
        "fade-in": "fade-in 0.2s ease-out both",
        "slide-up": "slide-up 0.28s cubic-bezier(0.2, 0.7, 0.2, 1) both",
        "scale-in": "scale-in 0.18s ease-out both",
        dot: "dot 1.2s ease-in-out infinite",
        "toast-in": "toast-in 0.25s ease-out both",
      },
      colors: {
        // Theme-aware: values live in src/index.css and flip with [data-theme].
        surface: v("surface"),
        slate: scale("slate", [50, 100, 200, 300, 400, 500, 600, 700, 800, 900]),
        brand: { 500: v("brand-500"), 600: v("brand-600"), link: v("brand-link") },
        danger: { DEFAULT: v("danger"), hover: v("danger-hover") },
        red: scale("red", [50, 100, 200, 500, 600, 700]),
        amber: scale("amber", [50, 500, 700]),
        emerald: scale("emerald", [50, 500, 700]),
      },
    },
  },
  plugins: [],
};
