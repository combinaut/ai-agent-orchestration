import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { FakeI18n, Locale, LocaleCatalog } from "#test/types";

export const LOCALES: readonly Locale[] = ["de", "en", "es", "fr", "pt"];

const LOCALES_DIR = join(import.meta.dirname, "../../public/_locales");
const TOKEN = /\$(\$|[A-Za-z0-9_@]+)\$/g;

export function readCatalog(locale: string): LocaleCatalog {
  const path = join(LOCALES_DIR, locale, "messages.json");

  return JSON.parse(readFileSync(path, "utf8")) as LocaleCatalog;
}

function positionOf(content: string): number {
  return Number(content.replace("$", "")) - 1;
}

// Chrome's substitution: $NAME$ takes the placeholder's $n content, $$ is a dollar sign.
function render(catalog: LocaleCatalog, key: string, substitutions: string[]): string {
  const entry = catalog[key];

  if (!entry) {
    return "";
  }

  const placeholders = new Map(
    Object.entries(entry.placeholders ?? {}).map(([name, one]) => [name.toLowerCase(), one]),
  );

  return entry.message.replaceAll(TOKEN, (_token, name: string) => {
    const placeholder = placeholders.get(name.toLowerCase());

    if (name === "$") {
      return "$";
    }

    return placeholder ? (substitutions[positionOf(placeholder.content)] ?? "") : "";
  });
}

// chrome.i18n backed by the real catalog of a locale.
export function createI18n(locale: Locale = "en"): FakeI18n {
  const catalog = readCatalog(locale);

  function getMessage(key: string, substitutions: string | string[] = []): string {
    return render(catalog, key, [substitutions].flat());
  }

  function getUILanguage(): string {
    return locale;
  }

  return { getMessage, getUILanguage };
}
