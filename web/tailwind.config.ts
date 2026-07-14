import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#0b1020",
        panel: "#141a2e",
        panel2: "#1b2340",
        line: "#2a3358",
        brand: "#6366f1",
        brand2: "#22d3ee",
      },
      boxShadow: {
        card: "0 8px 30px rgba(0,0,0,0.35)",
      },
    },
  },
  plugins: [],
};
export default config;
