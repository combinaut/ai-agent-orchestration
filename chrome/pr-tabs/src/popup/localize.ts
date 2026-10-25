import { getPlainMessage } from "#/i18n";

// The popup document takes the extension name as title and the browser UI language.
export function localizeDocument(target: Document): void {
  target.title = getPlainMessage("extName");
  target.documentElement.lang = chrome.i18n.getUILanguage();
}
