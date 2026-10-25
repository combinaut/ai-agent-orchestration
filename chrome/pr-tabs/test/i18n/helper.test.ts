// @vitest-environment happy-dom
import assert from "node:assert/strict";
import { afterEach, test, vi } from "vitest";
import { useMessages } from "#/composables/useMessages";
import { getMessage, getPlainMessage, getPluralMessage, listText } from "#/i18n";
import { checkText, duplicateText, openAllText, summaryText } from "#/texts";
import { localizeDocument } from "#popup/localize";
import type { Locale } from "#test/types";
import { LOCALES, createI18n, readCatalog } from "./catalog";

afterEach(() => {
  vi.unstubAllGlobals();
});

function speak(locale: Locale): void {
  vi.stubGlobal("chrome", { i18n: createI18n(locale) });
}

test("getMessage fills placeholders in the order of the English entry, whatever the locale", () => {
  speak("de");

  assert.equal(getMessage("errNotAPr", { org: "acme" }), "Keine PR-Nummer oder -URL von acme");
  assert.equal(
    getMessage("summaryLine", { prs: "3 PRs", groups: "2 Gruppen" }),
    "3 PRs in 2 Gruppen",
  );
});

test("getPluralMessage picks the form by the UI language's plural rule", () => {
  assert.equal(getPluralMessage("prCount", 1), "1 PR");
  assert.equal(getPluralMessage("prCount", 2), "2 PRs");
  assert.equal(getPluralMessage("inViewKept", 2, { prs: "7, 9" }), "2 tabs in view kept (7, 9)");

  speak("fr");

  assert.equal(getPluralMessage("groupCount", 0), "0 groupe");
  assert.equal(getPluralMessage("groupCount", 1), "1 groupe");
  assert.equal(getPluralMessage("groupCount", 2), "2 groupes");
});

test("listText joins with the conjunction of the UI language", () => {
  assert.equal(listText(["a", "b"]), "a and b");

  speak("de");

  assert.equal(listText(["a", "b"]), "a und b");

  speak("es");

  assert.equal(listText(["a", "b"]), "a y b");
});

test("useMessages returns the text of each plain key", () => {
  speak("pt");

  const { goToLabel, groupsLabel } = useMessages(["goToLabel", "groupsLabel"]);

  assert.equal(goToLabel, "Ir para um PR");
  assert.equal(groupsLabel, "Grupos de abas de PR");
});

test("localizeDocument sets the title to the name and the language to the UI language", () => {
  speak("fr");
  localizeDocument(document);

  assert.equal(document.title, "Orch Tabs");
  assert.equal(document.documentElement.lang, "fr");
});

test("an unsupported UI language formats and labels by the English catalog it falls back to", () => {
  vi.stubGlobal("chrome", { i18n: createI18n("en", "ja") });
  localizeDocument(document);

  assert.equal(getPluralMessage("prCount", 1), "1 PR");
  assert.equal(listText(["a", "b"]), "a and b");
  assert.equal(document.documentElement.lang, "en");
});

test("a regional UI language formats by the catalog language", () => {
  vi.stubGlobal("chrome", { i18n: createI18n("pt", "pt-BR") });
  localizeDocument(document);

  assert.equal(listText(["a", "b"]), "a e b");
  assert.equal(document.documentElement.lang, "pt");
});

test("the status lines follow the UI language", () => {
  speak("es");

  assert.equal(duplicateText(2), "Se cerraron 2 pestañas de PR duplicadas.");
  assert.equal(
    summaryText({ prs: 1, groups: [{ title: "a", count: 1, prs: [] }] }),
    "1 PR en 1 grupo",
  );
  assert.equal(
    checkText({
      tabs: 3,
      prs: 3,
      checked: 3,
      closed: [
        { key: "acme/webapp#5", state: "MERGED" },
        { key: "acme/webapp#6", state: "CLOSED" },
      ],
      inView: [],
    }),
    "Cerradas: 1 pestaña fusionada y 1 pestaña de PR cerrado.",
  );
  assert.equal(
    openAllText({ opened: 2, remaining: 5, estimated: true }, ""),
    "Abiertos: 2. Unos 5 más sin abrir; vuelve a pulsar para el siguiente lote.",
  );
});

for (const locale of LOCALES) {
  test(`every ${locale} message renders with its placeholders filled and none left over`, () => {
    speak(locale);

    for (const [key, entry] of Object.entries(readCatalog(locale))) {
      const names = Object.keys(entry.placeholders ?? {});
      const values = names.map((name) => `<${name}>`);
      const text = createI18n(locale).getMessage(key, values);

      assert.ok(text.length > 0, `${locale}.${key} is empty`);
      assert.ok(!text.includes("$"), `${locale}.${key} left a token: ${text}`);

      for (const value of values) {
        assert.ok(text.includes(value), `${locale}.${key} dropped ${value}`);
      }
    }
  });
}

// Never run; vue-tsc fails when any @ts-expect-error below stops expecting an error.
export function wrongCalls(): string[] {
  return [
    // @ts-expect-error the key is not in the catalog
    getMessage("goToLable"),
    // @ts-expect-error errNotAPr needs its org placeholder
    getMessage("errNotAPr"),
    // @ts-expect-error the placeholder is org, not organisation
    getMessage("errNotAPr", { organisation: "acme" }),
    // @ts-expect-error goToLabel has no placeholders
    getMessage("goToLabel", { org: "acme" }),
    // @ts-expect-error errNotAPr has placeholders, so it is not a plain key
    getPlainMessage("errNotAPr"),
    // @ts-expect-error there is no groupSize_one / groupSize_other pair
    getPluralMessage("groupSize", 2),
  ];
}
