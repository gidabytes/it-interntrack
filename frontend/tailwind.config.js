/** @type {import('tailwindcss').Config} */

// Colours are declared as space-separated RGB channels in index.css and read
// here through rgb(... / <alpha-value>), so every Tailwind opacity modifier
// (bg-primary/10, border-danger/40) keeps working while the underlying value
// flips with the theme. A plain `var(--x)` holding "#007AFF" would break those
// modifiers, which is why the channel form is used throughout.
const token = (name) => `rgb(var(${name}) / <alpha-value>)`;

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        // Surfaces, following Apple's grouped-background model: the page sits
        // a step back and content sits on raised cards, which is the inverse
        // of the usual web convention and the reason iOS lists read so
        // cleanly.
        canvas: token("--c-canvas"),
        elevated: token("--c-elevated"),
        raised: token("--c-raised"),
        sidebar: token("--c-sidebar"),

        // Label hierarchy. Four weights, as in UIKit: primary text, secondary
        // captions, tertiary hints, and quaternary for disabled glyphs.
        label: token("--c-label"),
        "label-2": token("--c-label-2"),
        "label-3": token("--c-label-3"),
        "label-4": token("--c-label-4"),

        separator: token("--c-separator"),
        "separator-opaque": token("--c-separator-opaque"),

        // Fills for pressed and hovered states, which must darken in light
        // mode and lighten in dark mode - the single biggest reason the old
        // hard-coded bg-white/5 looked invisible on a white page.
        fill: token("--c-fill"),
        "fill-2": token("--c-fill-2"),
        "fill-3": token("--c-fill-3"),

        // Apple's system colours, which differ between light and dark so that
        // each keeps its contrast against the background behind it.
        primary: {
          DEFAULT: token("--c-primary"),
          hover: token("--c-primary-hover"),
          soft: token("--c-primary-soft"),
          // For accent text on the accent's own soft wash, where the vivid
          // value does not reach 4.5:1.
          strong: token("--c-primary-strong"),
        },
        success: { DEFAULT: token("--c-success"), soft: token("--c-success-soft") },
        warning: { DEFAULT: token("--c-warning"), soft: token("--c-warning-soft") },
        danger: { DEFAULT: token("--c-danger"), soft: token("--c-danger-soft") },
        indigo: {
          DEFAULT: token("--c-indigo"),
          soft: token("--c-indigo-soft"),
          strong: token("--c-indigo-strong"),
        },
        teal: { DEFAULT: token("--c-teal"), soft: token("--c-teal-soft") },

        // Kept so any stray reference still resolves to something sensible
        // rather than silently rendering transparent.
        base: token("--c-canvas"),
        card: token("--c-elevated"),
        edge: token("--c-separator"),
      },

      // iOS 27 made corner radii uniform across windows and controls after the
      // inconsistency in 26 drew complaints. One scale, used everywhere.
      borderRadius: {
        ios: "0.625rem",      // 10px - buttons, inputs, small controls
        "ios-md": "0.875rem", // 14px - cards, grouped list containers
        "ios-lg": "1.25rem",  // 20px - sheets, modals
        "ios-xl": "1.75rem",  // 28px - full-screen panels
      },

      fontFamily: {
        // The San Francisco stack. On Apple hardware this resolves to SF Pro
        // itself; elsewhere it falls through to the platform UI font rather
        // than a downloaded webfont, which is both faster and more native.
        sans: [
          "-apple-system", "BlinkMacSystemFont", "SF Pro Text", "SF Pro Display",
          "Inter var", "Inter", "Segoe UI Variable", "Segoe UI", "system-ui",
          "Roboto", "Helvetica Neue", "sans-serif",
        ],
        mono: [
          "ui-monospace", "SF Mono", "SFMono-Regular", "Menlo",
          "Cascadia Mono", "Consolas", "monospace",
        ],
      },

      // Apple's text styles. Tracking tightens as size grows, which is what
      // stops large headings looking loose.
      fontSize: {
        "ios-caption2": ["0.6875rem", { lineHeight: "0.8125rem", letterSpacing: "0.006em" }],
        "ios-caption": ["0.75rem", { lineHeight: "1rem", letterSpacing: "0" }],
        "ios-footnote": ["0.8125rem", { lineHeight: "1.125rem", letterSpacing: "-0.006em" }],
        "ios-subhead": ["0.9375rem", { lineHeight: "1.25rem", letterSpacing: "-0.011em" }],
        "ios-body": ["1.0625rem", { lineHeight: "1.375rem", letterSpacing: "-0.022em" }],
        "ios-title3": ["1.25rem", { lineHeight: "1.5625rem", letterSpacing: "-0.026em" }],
        "ios-title2": ["1.375rem", { lineHeight: "1.75rem", letterSpacing: "-0.026em" }],
        "ios-title1": ["1.75rem", { lineHeight: "2.125rem", letterSpacing: "-0.028em" }],
        "ios-large": ["2.125rem", { lineHeight: "2.5625rem", letterSpacing: "-0.032em" }],
      },

      boxShadow: {
        // Apple's elevation is barely-there: a wide soft shadow plus a hairline,
        // never the heavy drop shadows common on the web.
        ios: "0 1px 2px rgb(var(--shadow) / 0.04), 0 4px 16px rgb(var(--shadow) / 0.05)",
        "ios-md": "0 2px 6px rgb(var(--shadow) / 0.06), 0 12px 32px rgb(var(--shadow) / 0.08)",
        "ios-lg": "0 8px 24px rgb(var(--shadow) / 0.10), 0 24px 56px rgb(var(--shadow) / 0.12)",
      },

      transitionTimingFunction: {
        // Apple's standard ease, which starts fast and settles slowly.
        ios: "cubic-bezier(0.25, 0.1, 0.25, 1)",
        spring: "cubic-bezier(0.34, 1.26, 0.64, 1)",
      },

      backdropBlur: { ios: "20px", "ios-lg": "40px" },
    },
  },
  plugins: [],
};
