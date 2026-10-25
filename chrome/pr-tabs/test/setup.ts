import { createI18n } from "#test/i18n/catalog";

globalThis.chrome = { i18n: createI18n("en") } as unknown as typeof chrome;
