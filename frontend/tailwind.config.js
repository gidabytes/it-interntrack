/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        // The palette from the specification.
        base: "#080B12",
        sidebar: "#101521",
        card: "#151D2B",
        edge: "#252C3A",
        primary: {
          DEFAULT: "#3B82F6",
          hover: "#2563EB",
          soft: "rgba(59,130,246,0.12)",
        },
        success: { DEFAULT: "#22C55E", soft: "rgba(34,197,94,0.12)" },
        warning: { DEFAULT: "#F59E0B", soft: "rgba(245,158,11,0.12)" },
        danger: { DEFAULT: "#EF4444", soft: "rgba(239,68,68,0.12)" },
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "-apple-system", "Segoe UI", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
    },
  },
  plugins: [],
};
