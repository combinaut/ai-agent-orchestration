export interface FileNode {
  path: string;
  element: HTMLElement;
}

export interface FileView {
  root: HTMLElement;
  files: FileNode[];
  tree: FileNode[];
}

function classicFiles(root: HTMLElement): FileNode[] {
  return [...root.querySelectorAll<HTMLElement>(".file[id^='diff-']")].flatMap((file) => {
    const header = file.querySelector<HTMLElement>(".file-header[data-path]");
    const path = header?.dataset.path;
    const element = file.closest<HTMLElement>("copilot-diff-entry") ?? file;

    return path ? [{ path, element }] : [];
  });
}

function reactFile(entry: Element): FileNode[] {
  const region = entry.querySelector<HTMLElement>("[role='region'][id^='diff-'][aria-labelledby]");
  if (!region || !(entry instanceof HTMLElement)) {
    return [];
  }
  const heading = region.querySelector<HTMLElement>("h3");
  const path = headingPath(heading);
  const recognized = heading?.id === region.getAttribute("aria-labelledby");

  return path && recognized ? [{ path, element: entry }] : [];
}

function reactFiles(root: HTMLElement): FileNode[] {
  return [...root.children].flatMap(reactFile);
}

function fileAnchor(file: FileNode): string | undefined {
  return file.element.matches("[id^='diff-']")
    ? file.element.id
    : file.element.querySelector("[id^='diff-']")?.id;
}

function treeFiles(doc: Document, files: FileNode[]): FileNode[] {
  const paths = new Map(files.map((file) => [fileAnchor(file), file.path]));
  const rows = doc.querySelectorAll<HTMLElement>(
    "#pr-file-tree [role='treeitem']:not([aria-expanded]), file-tree [role='treeitem']:not([aria-expanded])",
  );

  return [...rows].flatMap((element) => {
    const href = element.querySelector("a[href*='#diff-']")?.getAttribute("href");
    const anchor = href?.split("#").at(-1);
    const path =
      paths.get(anchor) ?? (element.closest("#pr-file-tree") && anchor ? element.id : "");

    return path ? [{ path, element }] : [];
  });
}

export function readFileView(doc: Document): FileView | null {
  const classic = doc.querySelector<HTMLElement>("#files.diff-view");
  const react = doc.querySelector<HTMLElement>(
    "#diff-comparison-viewer-container [data-testid='progressive-diffs-list']",
  );
  const root = classic ?? react;
  if (!root) {
    return null;
  }
  const files = classic ? classicFiles(root) : reactFiles(root);
  if (files.length === 0) {
    return null;
  }

  return { root, files, tree: treeFiles(doc, files) };
}

function headingPath(heading: HTMLElement | null): string | undefined {
  return heading?.querySelector("a code")?.textContent?.replace(/^\u200e|\u200e$/g, "");
}
