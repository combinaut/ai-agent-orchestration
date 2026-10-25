import en from "#locales/en/messages.json";
import type {
  CatalogEntry,
  MessageArguments,
  MessageKey,
  NamedValues,
  PlainMessageKey,
  PluralArguments,
  PluralBase,
  PluralForm,
} from "#/types";

// Placeholder order in the English entry fixes the $1, $2… positions in every locale.
const CATALOG: Readonly<Record<MessageKey, CatalogEntry>> = en;

function lookup(key: MessageKey, named: NamedValues = {}): string {
  const names = Object.keys(CATALOG[key].placeholders ?? {});

  return chrome.i18n.getMessage(
    key,
    names.map((name) => String(named[name])),
  );
}

export function getPlainMessage(key: PlainMessageKey): string {
  return lookup(key);
}

// The language of the catalog Chrome resolved, which is en when the UI language has none.
export function catalogLanguage(): string {
  return lookup("langTag");
}

export function getMessage<K extends MessageKey>(key: K, ...values: MessageArguments<K>): string {
  return lookup(key, values.at(0));
}

function pluralForm(count: number): PluralForm {
  const rules = new Intl.PluralRules(catalogLanguage());

  return rules.select(count) === "one" ? "one" : "other";
}

// Picks the _one or _other key of base by the catalog language's plural rule; count fills $COUNT$.
export function getPluralMessage<B extends PluralBase>(
  base: B,
  count: number,
  ...extras: PluralArguments<B>
): string {
  const key: MessageKey = `${base}_${pluralForm(count)}`;

  return lookup(key, { ...extras.at(0), count });
}

export function listText(items: string[]): string {
  return new Intl.ListFormat(catalogLanguage(), { type: "conjunction" }).format(items);
}
