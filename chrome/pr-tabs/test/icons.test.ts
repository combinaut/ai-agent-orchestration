import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { test } from "vitest";
import { ICON_SIZES, iconPath, renderIcon } from "#build/icons";
import type { IconManifest, PngSize } from "#test/types";

const EXTENSION = new URL("../extension/", import.meta.url);
const PNG_SIGNATURE = "89504e470d0a1a0a";

function pngSize(png: Buffer): PngSize {
  assert.equal(png.subarray(0, 8).toString("hex"), PNG_SIGNATURE);

  return { width: png.readUInt32BE(16), height: png.readUInt32BE(20) };
}

function builtManifest(): IconManifest {
  return JSON.parse(readFileSync(new URL("manifest.json", EXTENSION), "utf8")) as IconManifest;
}

function assertIconFiles(icons: chrome.runtime.ManifestIcons | string | undefined): void {
  assert.ok(typeof icons === "object", "icon map missing");
  assert.deepEqual(Object.keys(icons).map(Number), [...ICON_SIZES]);

  for (const size of ICON_SIZES) {
    assert.equal(icons[size], iconPath(size));
    assert.ok(existsSync(new URL(iconPath(size), EXTENSION)), `${iconPath(size)} missing`);
  }
}

test("the build renders every icon size at its pixel size", () => {
  for (const size of ICON_SIZES) {
    assert.deepEqual(pngSize(renderIcon(size)), { width: size, height: size });
  }
});

test("every committed icon exists at its pixel size", () => {
  for (const size of ICON_SIZES) {
    const png = readFileSync(new URL(iconPath(size), EXTENSION));

    assert.deepEqual(pngSize(png), { width: size, height: size }, iconPath(size));
  }
});

test("the manifest icons and toolbar icon point at existing files", () => {
  const manifest = builtManifest();

  assert.ok(manifest.action, "action missing");
  assertIconFiles(manifest.icons);
  assertIconFiles(manifest.action.default_icon);
});
