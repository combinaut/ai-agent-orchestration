import { readFileSync } from "node:fs";
import type { Rules } from "#/core";

export const rules: Rules = {
  ...(JSON.parse(readFileSync(new URL("../public/rules.json", import.meta.url), "utf8")) as Rules),
  org: "acme",
  defaultRepo: "webapp",
};
