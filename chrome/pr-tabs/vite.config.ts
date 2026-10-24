import tailwindcss from "@tailwindcss/vite";
import vue from "@vitejs/plugin-vue";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import type { Alias } from "vite";
import { defineConfig } from "vitest/config";

const path = (relative: string) => fileURLToPath(new URL(relative, import.meta.url));

const { imports } = JSON.parse(readFileSync(path("package.json"), "utf8")) as {
  imports: Record<string, string>;
};

// package.json imports ("#/*": "./src/*") as Vite aliases, so build and tests resolve them alike.
function alias(entry: [string, string]): Alias {
  const [name, target] = entry;

  return {
    find: new RegExp(`^${name.replace("*", "(.*)")}$`),
    replacement: path(target).replace("*", "$1"),
  };
}

const aliases = Object.entries(imports).map(alias);

export default defineConfig({
  root: path("src"),
  base: "./",
  publicDir: path("public"),
  plugins: [vue(), tailwindcss()],
  resolve: { alias: aliases },
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
    coverage: {
      enabled: true,
      provider: "v8",
      include: ["src/**/*.{ts,vue}"],
      reporter: ["text-summary"],
      thresholds: { lines: 80, branches: 80 },
    },
  },
});
