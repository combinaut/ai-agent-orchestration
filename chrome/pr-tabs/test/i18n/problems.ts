import type { LocaleCatalog } from "#test/types";

const TOKEN = /\$([A-Za-z0-9_@]+)\$/g;

function tokensOf(text: string): string[] {
  return [...new Set([...text.matchAll(TOKEN)].map((match) => (match[1] ?? "").toLowerCase()))];
}

function placeholdersOf(catalog: LocaleCatalog, key: string): Record<string, string> {
  const entries = Object.entries(catalog[key]?.placeholders ?? {});

  return Object.fromEntries(entries.map(([name, one]) => [name.toLowerCase(), one.content]));
}

function entryProblems(reference: LocaleCatalog, candidate: LocaleCatalog, key: string): string[] {
  const expected = placeholdersOf(reference, key);
  const actual = placeholdersOf(candidate, key);
  const message = candidate[key]?.message ?? "";
  const found: string[] = [];

  if (!message.trim()) {
    found.push(`${key}: empty message`);
  }

  if (JSON.stringify(expected) !== JSON.stringify(actual)) {
    found.push(
      `${key}: placeholders ${JSON.stringify(actual)} differ from ${JSON.stringify(expected)}`,
    );
  }

  if (tokensOf(message).toSorted().join() !== Object.keys(actual).toSorted().join()) {
    found.push(`${key}: message uses $${tokensOf(message).join("$, $")}$ not the placeholders`);
  }

  return found;
}

// Every way candidate differs from the reference catalog: missing, extra, placeholders, empties.
export function catalogProblems(reference: LocaleCatalog, candidate: LocaleCatalog): string[] {
  const keys = Object.keys(reference);
  const missing = keys.filter((key) => !(key in candidate)).map((key) => `missing ${key}`);
  const extra = Object.keys(candidate)
    .filter((key) => !(key in reference))
    .map((key) => `extra ${key}`);
  const shared = keys.filter((key) => key in candidate);

  return [
    ...missing,
    ...extra,
    ...shared.flatMap((key) => entryProblems(reference, candidate, key)),
  ];
}
