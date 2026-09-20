/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: ["class", '[data-theme="dark"]'],
  theme: {
    extend: {
      // Every colour resolves to a CSS custom property defined in index.css.
      // That is what makes the light and dark themes two complete designs rather
      // than one design with the colours inverted: each variable is chosen
      // deliberately for its own mode.
      colors: {
        canvas: "rgb(var(--canvas) / <alpha-value>)",
        surface: "rgb(var(--surface) / <alpha-value>)",
        raised: "rgb(var(--raised) / <alpha-value>)",
        sunken: "rgb(var(--sunken) / <alpha-value>)",
        line: "rgb(var(--line) / <alpha-value>)",
        "line-strong": "rgb(var(--line-strong) / <alpha-value>)",
        ink: "rgb(var(--ink) / <alpha-value>)",
        muted: "rgb(var(--muted) / <alpha-value>)",
        faint: "rgb(var(--faint) / <alpha-value>)",
        brand: {
          DEFAULT: "rgb(var(--brand) / <alpha-value>)",
          soft: "rgb(var(--brand-soft) / <alpha-value>)",
          ink: "rgb(var(--brand-ink) / <alpha-value>)",
        },
        accent: {
          DEFAULT: "rgb(var(--accent) / <alpha-value>)",
          soft: "rgb(var(--accent-soft) / <alpha-value>)",
        },
        positive: "rgb(var(--positive) / <alpha-value>)",
        caution: "rgb(var(--caution) / <alpha-value>)",
        negative: "rgb(var(--negative) / <alpha-value>)",
        info: "rgb(var(--info) / <alpha-value>)",
      },
      fontFamily: {
        sans: [
          "Inter var",
          "Inter",
          "-apple-system",
          "BlinkMacSystemFont",
          "Segoe UI",
          "Roboto",
          "Helvetica Neue",
          "Arial",
          "sans-serif",
        ],
        mono: [
          "ui-monospace",
          "SFMono-Regular",
          "JetBrains Mono",
          "Menlo",
          "Consolas",
          "monospace",
        ],
        // Used for the wordmark and large display headings.
        display: [
          "Inter var",
          "Inter",
          "-apple-system",
          "BlinkMacSystemFont",
          "Segoe UI",
          "sans-serif",
        ],
      },
      fontSize: {
        "2xs": ["0.6875rem", { lineHeight: "1rem", letterSpacing: "0.01em" }],
      },
      // Tailwind's default scale skips 4.5 and 9.5. Both are needed: 9.5 (38px)
      // is the control height that keeps inputs and buttons visually matched
      // without being as tall as h-10, and 4.5 is the matching half-step.
      spacing: {
        4.5: "1.125rem",
        9.5: "2.375rem",
      },
      borderRadius: {
        xl: "0.875rem",
        "2xl": "1.125rem",
        "3xl": "1.5rem",
      },
      boxShadow: {
        // Layered, low-opacity shadows. A single hard shadow reads as "template";
        // two soft ones read as depth.
        card: "0 1px 2px rgb(var(--shadow) / 0.04), 0 4px 16px -4px rgb(var(--shadow) / 0.08)",
        lifted: "0 2px 4px rgb(var(--shadow) / 0.06), 0 12px 32px -8px rgb(var(--shadow) / 0.14)",
        pop: "0 8px 24px -6px rgb(var(--shadow) / 0.18), 0 2px 6px rgb(var(--shadow) / 0.08)",
        glow: "0 0 0 1px rgb(var(--brand) / 0.25), 0 0 28px -6px rgb(var(--brand) / 0.35)",
      },
      keyframes: {
        "fade-up": {
          from: { opacity: "0", transform: "translateY(6px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        "fade-in": { from: { opacity: "0" }, to: { opacity: "1" } },
        "scale-in": {
          from: { opacity: "0", transform: "scale(0.97)" },
          to: { opacity: "1", transform: "scale(1)" },
        },
        shimmer: {
          "0%": { backgroundPosition: "-200% 0" },
          "100%": { backgroundPosition: "200% 0" },
        },
        "pulse-ring": {
          "0%": { transform: "scale(0.9)", opacity: "0.7" },
          "70%": { transform: "scale(1.5)", opacity: "0" },
          "100%": { transform: "scale(1.5)", opacity: "0" },
        },
        "slide-down": {
          from: { opacity: "0", maxHeight: "0" },
          to: { opacity: "1", maxHeight: "1200px" },
        },
        "trace-flow": {
          "0%": { strokeDashoffset: "24" },
          "100%": { strokeDashoffset: "0" },
        },
      },
      animation: {
        "fade-up": "fade-up 0.32s cubic-bezier(0.22, 1, 0.36, 1) both",
        "fade-in": "fade-in 0.24s ease-out both",
        "scale-in": "scale-in 0.2s cubic-bezier(0.22, 1, 0.36, 1) both",
        shimmer: "shimmer 1.8s linear infinite",
        "pulse-ring": "pulse-ring 1.6s cubic-bezier(0.4, 0, 0.6, 1) infinite",
        "slide-down": "slide-down 0.28s cubic-bezier(0.22, 1, 0.36, 1) both",
        "trace-flow": "trace-flow 1s linear infinite",
      },
      transitionTimingFunction: {
        smooth: "cubic-bezier(0.22, 1, 0.36, 1)",
      },
    },
  },
  plugins: [],
};
