import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // rgb(<channels> / <alpha-value>) lets opacity modifiers like bg-accent/10 work
        accent: "rgb(var(--accent-rgb) / <alpha-value>)",
        "accent-action": "rgb(var(--accent-action-rgb) / <alpha-value>)",
        teal: "rgb(var(--teal-rgb) / <alpha-value>)",
      },
      fontFamily: {
        display: ["var(--font-display)"],
        body: ["var(--font-body)"],
      },
    },
  },
  plugins: [],
} satisfies Config;
