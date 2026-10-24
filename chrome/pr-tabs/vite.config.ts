import tailwindcss from "@tailwindcss/vite";
import vue from "@vitejs/plugin-vue";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

const path = (relative: string) => fileURLToPath(new URL(relative, import.meta.url));

export default defineConfig({
  root: path("src"),
  base: "./",
  publicDir: path("public"),
  plugins: [vue(), tailwindcss()],
  build: {
    outDir: path("extension"),
    emptyOutDir: true,
    modulePreload: { polyfill: false },
    reportCompressedSize: false,
    rolldownOptions: {
      input: { popup: path("src/popup.html"), background: path("src/background.ts") },
      output: {
        entryFileNames: "[name].js",
        chunkFileNames: "shared.js",
        assetFileNames: "[name][extname]",
      },
    },
  },
  test: {
    root: path("."),
    include: ["test/**/*.test.ts"],
  },
});
