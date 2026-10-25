import { catalogLanguage, getPlainMessage } from "#/i18n";

// The popup document takes the extension name as title and the language of the catalog in use.
export function localizeDocument(target: Document): void {
  target.title = getPlainMessage("extName");
  target.documentElement.lang = catalogLanguage();
}
