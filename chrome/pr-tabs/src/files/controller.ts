import { getMessage } from "#/i18n";
import { readFileView } from "#/files/dom";
import type { FileView } from "#/files/dom";
import { hiddenPaths } from "#/files/matcher";
import type { FileSettings } from "#/files/settings";

const HIDDEN = "data-pr-tabs-hidden";

class FileFilter {
  private settings: FileSettings = { enabled: false, patterns: [] };
  private page: string;
  private revealed = false;
  private owned = new Set<HTMLElement>();
  private bar: HTMLElement;
  private label: HTMLElement;
  private toggle: HTMLButtonElement;

  constructor(
    private doc: Document,
    private currentUrl: () => string,
  ) {
    this.page = pageUrl(currentUrl());
    this.bar = doc.createElement("div");
    this.label = doc.createElement("span");
    this.toggle = doc.createElement("button");
    this.bar.id = "pr-tabs-hidden-files";
    this.bar.className = "flash mb-3";
    this.bar.setAttribute("role", "status");
    this.toggle.type = "button";
    this.toggle.className = "btn-link ml-2";
    this.toggle.textContent = getMessage("showHiddenFiles");
    this.toggle.addEventListener("click", () => {
      this.revealed = !this.revealed;
      this.update();
    });
    this.bar.append(this.label, this.toggle);
  }

  private clearHidden(): void {
    for (const element of this.owned) {
      element.removeAttribute(HIDDEN);
    }
    this.owned.clear();
  }

  restore = (): void => {
    this.clearHidden();
    this.bar.remove();
  };

  update = (): void => {
    const url = pageUrl(this.currentUrl());
    if (url !== this.page) {
      this.revealed = false;
      this.page = url;
    }
    this.clearHidden();
    const active = this.settings.enabled && isFilesPage(url);
    const view = active ? readFileView(this.doc) : null;
    if (view) {
      this.apply(view);
    } else {
      this.bar.remove();
    }
  };

  private apply(view: FileView): void {
    const files = [...view.files, ...view.tree];
    const paths = hiddenPaths(
      files.map((file) => file.path),
      this.settings.patterns,
    );
    for (const file of files) {
      if (!this.revealed && paths.has(file.path)) {
        file.element.setAttribute(HIDDEN, "");
        this.owned.add(file.element);
      }
    }
    this.showBar(view, paths.size);
  }

  private showBar(view: FileView, count: number): void {
    if (count === 0) {
      this.bar.remove();

      return;
    }
    const label = this.revealed ? "hiddenFilesShown" : "filesHidden";
    const action = this.revealed ? "hideHiddenFiles" : "showHiddenFiles";
    this.label.textContent = getMessage(label, { count });
    this.toggle.textContent = getMessage(action);
    if (this.bar.nextSibling !== view.root) {
      view.root.before(this.bar);
    }
  }

  configure = (value: FileSettings): void => {
    this.settings = value;
    this.update();
  };
}

export function createFileFilter(doc: Document, currentUrl: () => string) {
  return new FileFilter(doc, currentUrl);
}

function isFilesPage(url: string): boolean {
  return /^\/[^/]+\/[^/]+\/pull\/\d+\/(?:files|changes)(?:\/|$)/.test(new URL(url).pathname);
}

function pageUrl(value: string): string {
  const url = new URL(value);
  url.hash = "";

  return url.href;
}
