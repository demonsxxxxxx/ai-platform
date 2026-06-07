/** @type {import('tailwindcss').Config} */
export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["'Source Sans 3'", "system-ui", "sans-serif"],
      },
      colors: {
        theme: {
          text: "var(--theme-text)",
          "text-secondary": "var(--theme-text-secondary)",
          bg: "var(--theme-bg)",
          "bg-card": "var(--theme-bg-card)",
          border: "var(--theme-border)",
          primary: "var(--theme-primary)",
          "primary-hover": "var(--theme-primary-hover)",
          "primary-light": "var(--theme-primary-light)",
        },
      },
    },
  },
  plugins: [],
};
