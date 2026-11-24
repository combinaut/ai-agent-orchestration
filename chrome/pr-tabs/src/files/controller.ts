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

  constructor(
    private doc: Document,
    private currentUrl: () => string,
  ) {
    this.page = pageUrl(currentUrl());
    this.bar = doc.createElement("div");
    this.label = doc.createElement("span");
    const show = doc.createElement("button");
    this.bar.id = "pr-tabs-hidden-files";
    this.bar.className = "flash mb-3";
    this.bar.setAttribute("role", "status");
    show.type = "button";
    show.className = "btn-link ml-2";
    show.textContent = getMessage("showHiddenFiles");
    show.addEventListener("click", () => {
      this.revealed = true;
      this.update();
    });
    this.bar.append(this.label, show);
  }

  restore = (): void => {
    for (const element of this.owned) {
      element.removeAttribute(HIDDEN);
    }
    this.owned.clear();
    this.bar.remove();
  };

  update = (): void => {
    const url = pageUrl(this.currentUrl());
    if (url !== this.page) {
      this.revealed = false;
      this.page = url;
    }
    this.restore();
    const active = this.settings.enabled && !this.revealed && isFilesPage(url);
    const view = active ? readFileView(this.doc) : null;
    if (view) {
      this.apply(view);
    }
  };

  private apply(view: FileView): void {
    const files = [...view.files, ...view.tree];
    const paths = hiddenPaths(
      files.map((file) => file.path),
      this.settings.patterns,
    );
    for (const file of files) {
      if (paths.has(file.path)) {
        file.element.setAttribute(HIDDEN, "");
        this.owned.add(file.element);
      }
    }
    if (paths.size > 0) {
      this.label.textContent = getMessage("filesHidden", { count: paths.size });
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
