import { Resvg } from "@resvg/resvg-js";
import { readFileSync } from "node:fs";
import type { Plugin } from "vite";
import type { IconSize } from "./types";

export const ICON_SIZES: readonly IconSize[] = [16, 32, 48, 128];

// 16 px has its own simplified drawing.
function iconSource(size: IconSize): URL {
  const name = size === 16 ? "icon-16.svg" : "icon.svg";

  return new URL(`../icons/${name}`, import.meta.url);
}

export function iconPath(size: IconSize): string {
  return `icons/icon-${size}.png`;
}

export function renderIcon(size: IconSize): Buffer {
  const svg = readFileSync(iconSource(size));
  const resvg = new Resvg(svg, { fitTo: { mode: "width", value: size } });

  return resvg.render().asPng();
}

export function extensionIcons(): Plugin {
  return {
    name: "pr-tabs-icons",
    generateBundle(): void {
      for (const size of ICON_SIZES) {
        this.emitFile({ type: "asset", fileName: iconPath(size), source: renderIcon(size) });
      }
    },
  };
}
