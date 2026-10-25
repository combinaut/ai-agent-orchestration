import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "vitest";
import { LOCALES, readCatalog } from "./catalog";
import { catalogProblems } from "./problems";

const ENGLISH = readCatalog("en");
const PUBLIC = join(import.meta.dirname, "../../public");
const NAME_LIMIT = 75;
const DESCRIPTION_LIMIT = 132;

test("the locale folders are exactly de, en, es, fr and pt", () => {
  assert.deepEqual(readdirSync(join(PUBLIC, "_locales")).toSorted(), [...LOCALES]);
});

for (const locale of LOCALES) {
  test(`${locale} has the keys and placeholders of en and no empty message`, () => {
    assert.deepEqual(catalogProblems(ENGLISH, readCatalog(locale)), []);
  });

  test(`${locale} names its own language in langTag`, () => {
    assert.equal(readCatalog(locale)["langTag"]?.message, locale);
  });

  test(`${locale} keeps the name Orch Tabs within the Chrome limits`, () => {
    const catalog = readCatalog(locale);

    assert.equal(catalog["extName"]?.message, "Orch Tabs");
    assert.ok((catalog["extDescription"]?.message.length ?? 0) <= DESCRIPTION_LIMIT);
    assert.ok((catalog["extName"]?.message.length ?? 0) <= NAME_LIMIT);
  });
}

test("each plural _one key has an _other key with the same placeholders", () => {
  const ones = Object.keys(ENGLISH).filter((key) => key.endsWith("_one"));
  const names = (key: string): string[] => Object.keys(ENGLISH[key]?.placeholders ?? {}).toSorted();

  assert.ok(ones.length > 0);

  for (const one of ones) {
    const other = one.replace(/_one$/, "_other");

    assert.ok(other in ENGLISH, `${one} has no ${other}`);
    assert.deepEqual(names(other), names(one));
  }
});

test("the manifest takes its name and description from the catalog", () => {
  const manifest = JSON.parse(readFileSync(join(PUBLIC, "manifest.json"), "utf8")) as {
    name: string;
    description: string;
    default_locale: string;
    action: { default_title: string };
  };

  assert.equal(manifest.default_locale, "en");
  assert.equal(manifest.name, "__MSG_extName__");
  assert.equal(manifest.description, "__MSG_extDescription__");
  assert.equal(manifest.action.default_title, "__MSG_extName__");
});

test("the parity check catches a missing key, an extra key and a renamed placeholder", () => {
  const { goToLabel: _dropped, ...rest } = ENGLISH;
  const broken = {
    ...rest,
    extra: { message: "x" },
    noPrTabs: { message: " " },
    prCount_one: { message: "$N$ PR", placeholders: { n: { content: "$1" } } },
  };

  assert.deepEqual(catalogProblems(ENGLISH, broken), [
    "missing goToLabel",
    "extra extra",
    "noPrTabs: empty message",
    `prCount_one: placeholders {"n":"$1"} differ from {"count":"$1"}`,
  ]);
});

test("the parity check catches a message that uses a token without a placeholder", () => {
  const broken = { ...ENGLISH, errNotAPr: { message: "Not a $ORG$ PR", placeholders: {} } };

  assert.deepEqual(catalogProblems(ENGLISH, broken), [
    `errNotAPr: placeholders {} differ from {"org":"$1"}`,
    "errNotAPr: message uses $org$ not the placeholders",
  ]);
});
