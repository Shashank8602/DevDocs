const v = (name) => `rgb(var(--${name}) / <alpha-value>)`;
const scale = (name, steps) => Object.fromEntries(steps.map((s) => [s, v(`${name}-${s}`)]));

/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: ["selector", '[data-theme="dark"]'],
  theme: {
    extend: {
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
