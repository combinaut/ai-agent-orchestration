import { readFileSync } from "node:fs";
export const rules = {
  ...JSON.parse(readFileSync(new URL("../extension/rules.json", import.meta.url))),
  org: "acme",
  defaultRepo: "webapp",
};
