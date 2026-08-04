/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{vue,js,ts,jsx,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        // 深色主题主色调
        bg: {
          DEFAULT: "#0f0f14",
          card: "#1a1a24",
          hover: "#24242f",
        },
        accent: {
          DEFAULT: "#8b5cf6",
          hover: "#7c3aed",
        },
      },
    },
  },
  plugins: [],
};
