// @vitest-environment happy-dom
import assert from "node:assert/strict";
import { join } from "node:path";
import { readFileSync } from "node:fs";
import { afterEach, test, vi } from "vitest";
import { createFileFilter } from "#/files/controller";
import { readFileView } from "#/files/dom";
import { hiddenPaths, matchesGlob } from "#/files/matcher";
import { observeFiles } from "#/files/observe";
import { DEFAULT_PATTERNS } from "#/files/settings";

const fixture = (name: string) =>
  readFileSync(join(import.meta.dirname, `fixtures/files-${name}.html`), "utf8");
const HIDDEN = "[data-pr-tabs-hidden]";
let page = "https://github.com/example/project/pull/1/files";

function setup(name = "react") {
  document.body.innerHTML = fixture(name);
  page = "https://github.com/example/project/pull/1/files";
  const filter = createFileFilter(document, () => page);
  filter.configure({ enabled: true, patterns: DEFAULT_PATTERNS });

  return filter;
}

afterEach(() => {
  document.body.innerHTML = "";
  vi.useRealTimers();
});

test("globs match full nested paths and treat only stars as wildcards", () => {
  for (const path of [
    "Button.stories.ts",
    "src/ui/Button.stories.ts",
    "spec/models/user_spec.rb",
    "src/unit.spec.ts",
  ]) {
    assert.equal(
      DEFAULT_PATTERNS.some((pattern) => matchesGlob(path, pattern)),
      true,
      path,
    );
  }
  for (const path of [
    "inspect.ts",
    "src/unitXspecYts",
    "src/unit.spec.tsx",
    "Button.stories",
    "src/unit.spec.ts/extra",
  ]) {
    assert.equal(
      DEFAULT_PATTERNS.some((pattern) => matchesGlob(path, pattern)),
      false,
      path,
    );
  }
  assert.equal(matchesGlob("src/[a]+(b)?.ts", "src/[a]+(b)?.ts"), true);
  assert.equal(matchesGlob("other/a.ts", "src/*.ts"), false);
  assert.equal(matchesGlob("", ""), false);
});

test("hidden paths are unique and empty patterns hide nothing", () => {
  assert.deepEqual(
    [...hiddenPaths(["a.spec.ts", "a.spec.ts", "inspect.ts"], ["*.ts", "*.spec.ts"])],
    ["a.spec.ts", "inspect.ts"],
  );
  assert.equal(hiddenPaths(["a.spec.ts"], []).size, 0);
});

for (const name of ["classic", "react"]) {
  test(`${name} hides complete diffs and leaf tree rows with a unique count`, () => {
    setup(name);
    assert.equal(document.querySelectorAll(HIDDEN).length, 2);
    assert.equal(
      document.querySelector("#pr-tabs-hidden-files span")?.textContent,
      "1 files hidden by PR Tabs ·",
    );
    assert.equal(document.querySelector("[data-keep]")?.hasAttribute("data-pr-tabs-hidden"), false);
  });
}

test("show them reveals this page including later arrivals until navigation", () => {
  const filter = setup();
  document.querySelector<HTMLButtonElement>("#pr-tabs-hidden-files button")?.click();
  page += "#diff-story";
  const root = document.querySelector("[data-testid=progressive-diffs-list]");
  root?.insertAdjacentHTML("beforeend", root.firstElementChild?.outerHTML ?? "");
  filter.update();
  assert.equal(document.querySelectorAll(HIDDEN).length, 0);
  assert.equal(
    document.querySelector("#pr-tabs-hidden-files span")?.textContent,
    "1 hidden files shown ·",
  );
  assert.equal(document.querySelector("#pr-tabs-hidden-files button")?.textContent, "Hide them");
  page = "https://github.com/example/project/pull/2/changes";
  filter.update();
  assert.equal(document.querySelectorAll(HIDDEN).length, 3);
});

test("settings changes restore owned elements and leave existing hidden state alone", () => {
  const filter = setup();
  const row = document.querySelector<HTMLElement>("[role=treeitem]");
  row?.setAttribute("hidden", "");
  filter.configure({ enabled: true, patterns: ["*.rb"] });
  assert.equal(document.querySelectorAll(HIDDEN).length, 0);
  filter.configure({ enabled: true, patterns: DEFAULT_PATTERNS });
  filter.configure({ enabled: false, patterns: DEFAULT_PATTERNS });
  assert.equal(document.querySelectorAll(HIDDEN).length, 0);
  assert.equal(row?.hidden, true);
});

test("unrecognized markup and non-files pages fail open without a bar", () => {
  const filter = setup();
  page = "https://github.com/example/project/pull/1";
  filter.update();
  assert.equal(document.querySelectorAll(HIDDEN).length, 0);
  page += "/files";
  document.body.innerHTML = '<div data-path="a.spec.ts"><div id="files"></div></div>';
  filter.update();
  assert.equal(readFileView(document), null);
  assert.equal(document.querySelector("#pr-tabs-hidden-files"), null);
});

test("observer hides late files, updates counts and does not observe its own writes", async () => {
  vi.useFakeTimers();
  const filter = setup();
  const update = vi.fn<() => void>(filter.update);
  const stop = observeFiles(document, update);
  const root = document.querySelector("[data-testid=progressive-diffs-list]");
  const entry = root?.firstElementChild?.outerHTML
    .replaceAll("src/Button.stories.ts", "src/other.spec.ts")
    .replaceAll("diff-story", "diff-other");
  root?.insertAdjacentHTML("beforeend", entry ?? "");
  await vi.advanceTimersByTimeAsync(60);
  assert.equal(document.querySelectorAll(HIDDEN).length, 3);
  assert.match(document.querySelector("#pr-tabs-hidden-files")?.textContent ?? "", /^2 files/);
  await vi.advanceTimersByTimeAsync(200);
  assert.equal(update.mock.calls.length, 1);
  stop();
});

test("observer follows turbo navigation and stops cleanly", async () => {
  vi.useFakeTimers();
  const filter = setup();
  const stop = observeFiles(document, filter.update);
  page = "https://github.com/example/project/pull/1";
  document.dispatchEvent(new Event("turbo:load"));
  await vi.advanceTimersByTimeAsync(60);
  assert.equal(document.querySelectorAll(HIDDEN).length, 0);
  stop();
  page += "/files";
  document.dispatchEvent(new Event("turbo:load"));
  await vi.advanceTimersByTimeAsync(60);
  assert.equal(document.querySelectorAll(HIDDEN).length, 0);
});

test("the bar toggles both ways with a stable unique count and keyboard focus", () => {
  setup();
  const button = document.querySelector<HTMLButtonElement>("#pr-tabs-hidden-files button");
  button?.focus();
  button?.click();
  assert.equal(document.querySelectorAll(HIDDEN).length, 0);
  assert.equal(
    document.querySelector("#pr-tabs-hidden-files span")?.textContent,
    "1 hidden files shown ·",
  );
  assert.equal(document.activeElement, button);
  button?.click();
  assert.equal(document.querySelectorAll(HIDDEN).length, 2);
  assert.equal(
    document.querySelector("#pr-tabs-hidden-files span")?.textContent,
    "1 files hidden by PR Tabs ·",
  );
  assert.equal(button?.textContent, "Show them");
  assert.equal(document.activeElement, button);
});

test("shown-mode counts update for late arrivals and live settings", () => {
  const filter = setup();
  document.querySelector<HTMLButtonElement>("#pr-tabs-hidden-files button")?.click();
  const root = document.querySelector("[data-testid=progressive-diffs-list]");
  root?.insertAdjacentHTML(
    "beforeend",
    root.firstElementChild?.outerHTML
      .replaceAll("src/Button.stories.ts", "src/other.spec.ts")
      .replaceAll("diff-story", "diff-other") ?? "",
  );
  filter.update();
  assert.equal(document.querySelectorAll(HIDDEN).length, 0);
  assert.equal(
    document.querySelector("#pr-tabs-hidden-files span")?.textContent,
    "2 hidden files shown ·",
  );
  filter.configure({ enabled: true, patterns: ["*.spec.ts"] });
  assert.equal(
    document.querySelector("#pr-tabs-hidden-files span")?.textContent,
    "1 hidden files shown ·",
  );
  document.querySelector<HTMLButtonElement>("#pr-tabs-hidden-files button")?.click();
  assert.equal(document.querySelectorAll(HIDDEN).length, 1);
});
