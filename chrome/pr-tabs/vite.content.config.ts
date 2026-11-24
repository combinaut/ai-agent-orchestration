import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
const path = (relative: string) => fileURLToPath(new URL(relative, import.meta.url));
export default defineConfig({
  resolve: { alias: { "#": path("src"), "#locales": path("public/_locales") } },
  publicDir: false,
  build: {
    outDir: path("extension"),
    emptyOutDir: false,
    reportCompressedSize: false,
    lib: {
      entry: path("src/files/content.ts"),
      name: "prTabsFiles",
      formats: ["iife"],
      fileName: () => "files.js",
    },
  },
});
