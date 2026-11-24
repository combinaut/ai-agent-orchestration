// @vitest-environment happy-dom
import assert from "node:assert/strict";
import { flushPromises } from "@vue/test-utils";
import { afterEach, test, vi } from "vitest";
import { mountPopup as mount } from "#test/components/mountPopup";
import App from "#/options/App.vue";
import {
  DEFAULT_PATTERNS,
  FILE_SETTINGS_KEY,
  loadFileSettings,
  parseFileSettings,
  saveFileSettings,
  watchFileSettings,
} from "#/files/settings";

const original = chrome;
const get = vi.fn<() => Promise<Record<string, unknown>>>();
const set = vi.fn<(settings: Record<string, unknown>) => Promise<void>>();
type Listener = (changes: Record<string, unknown>, area: string) => void;
const addListener = vi.fn<(listener: Listener) => void>();
const removeListener = vi.fn<(listener: Listener) => void>();

function storage(value: unknown = undefined): void {
  get.mockReset().mockResolvedValue({ [FILE_SETTINGS_KEY]: value });
  set.mockReset().mockResolvedValue(undefined);
  addListener.mockReset();
  removeListener.mockReset();
  globalThis.chrome = {
    ...original,
    storage: { sync: { get, set }, onChanged: { addListener, removeListener } },
  } as unknown as typeof chrome;
}

afterEach(() => {
  globalThis.chrome = original;
});

test("storage defaults are opt in and malformed settings retain default patterns", async () => {
  storage();
  assert.deepEqual(await loadFileSettings(), { enabled: false, patterns: DEFAULT_PATTERNS });
  assert.deepEqual(parseFileSettings({ enabled: true, patterns: [1] }), {
    enabled: true,
    patterns: DEFAULT_PATTERNS,
  });
  assert.deepEqual(parseFileSettings({ enabled: "yes" }), {
    enabled: false,
    patterns: DEFAULT_PATTERNS,
  });
  assert.deepEqual(parseFileSettings({ enabled: true, patterns: [] }), {
    enabled: true,
    patterns: [],
  });
});

test("settings save to sync and live listeners ignore other storage areas", async () => {
  storage();
  const settings = { enabled: true, patterns: ["*.spec.ts"] };
  await saveFileSettings(settings);
  assert.deepEqual(set.mock.calls, [[{ [FILE_SETTINGS_KEY]: settings }]]);
  const apply = vi.fn<(value: unknown) => void>();
  const stop = watchFileSettings(apply);
  const listener = addListener.mock.calls[0]?.[0] as (
    changes: Record<string, unknown>,
    area: string,
  ) => void;
  listener({ [FILE_SETTINGS_KEY]: { newValue: settings } }, "local");
  listener({ other: {} }, "sync");
  assert.equal(apply.mock.calls.length, 0);
  listener({ [FILE_SETTINGS_KEY]: { newValue: settings } }, "sync");
  assert.deepEqual(apply.mock.calls, [[settings]]);
  stop();
  assert.deepEqual(removeListener.mock.calls, [[listener]]);
});

test("options load defaults, toggle, add unique trimmed patterns and remove", async () => {
  storage();
  const wrapper = mount(App);
  assert.equal(wrapper.get("fieldset").attributes("disabled"), "");
  await flushPromises();
  assert.equal(wrapper.findAll("li").length, 3);
  await wrapper.get<HTMLInputElement>('input[type="checkbox"]').setValue(true);
  await wrapper.get("#file-pattern").setValue(" *.test.ts ");
  await wrapper.get("form").trigger("submit");
  await wrapper.get("#file-pattern").setValue("*.test.ts");
  await wrapper.get("form").trigger("submit");
  await wrapper.get("li button").trigger("click");
  await flushPromises();
  assert.deepEqual(parseFileSettings(set.mock.calls.at(-1)?.[0]?.[FILE_SETTINGS_KEY]), {
    enabled: true,
    patterns: ["*_spec.rb", "*.spec.ts", "*.test.ts"],
  });
  wrapper.unmount();
  assert.equal(removeListener.mock.calls.length, 1);
});

test("options show storage failures and keep a failed load disabled", async () => {
  storage();
  get.mockRejectedValue(new Error("unavailable"));
  const wrapper = mount(App);
  await flushPromises();
  assert.ok(wrapper.find('[role="alert"]').exists());
  assert.equal(wrapper.get("fieldset").attributes("disabled"), "");
  wrapper.unmount();
});

test("options report failed writes and accept live settings", async () => {
  storage();
  const wrapper = mount(App);
  await flushPromises();
  set.mockRejectedValue(new Error("quota"));
  await wrapper.get<HTMLInputElement>('input[type="checkbox"]').setValue(true);
  await flushPromises();
  assert.ok(wrapper.find('[role="alert"]').exists());
  const listener = addListener.mock.calls[0]?.[0] as (
    changes: Record<string, unknown>,
    area: string,
  ) => void;
  listener({ [FILE_SETTINGS_KEY]: { newValue: { enabled: false, patterns: ["*.rb"] } } }, "sync");
  await flushPromises();
  assert.equal(wrapper.findAll("li").length, 1);
  assert.equal(wrapper.get("code").text(), "*.rb");
  wrapper.unmount();
});

test("own storage echoes cannot discard a newer queued pattern edit", async () => {
  storage();
  const first = Promise.withResolvers<void>();
  set.mockImplementationOnce(() => first.promise);
  const wrapper = mount(App);
  await flushPromises();
  await wrapper.get<HTMLInputElement>('input[type="checkbox"]').setValue(true);
  const oldValue = set.mock.calls[0]?.[0]?.[FILE_SETTINGS_KEY];
  await wrapper.get("#file-pattern").setValue("*.test.ts");
  await wrapper.get("form").trigger("submit");
  const listener = addListener.mock.calls[0]?.[0];
  listener?.({ [FILE_SETTINGS_KEY]: { newValue: oldValue } }, "sync");
  await wrapper.get<HTMLInputElement>('input[type="checkbox"]').setValue(false);
  first.resolve();
  await flushPromises();
  assert.deepEqual(parseFileSettings(set.mock.calls.at(-1)?.[0]?.[FILE_SETTINGS_KEY]), {
    enabled: false,
    patterns: [...DEFAULT_PATTERNS, "*.test.ts"],
  });
  wrapper.unmount();
});

test("settings use branded PrimeVue controls and accessible icon-only remove buttons", async () => {
  storage();
  const wrapper = mount(App);
  await flushPromises();
  assert.equal(wrapper.get("h1").text(), "PR Tabs settings");
  assert.equal(wrapper.get(".settings-icon").attributes("src"), "./icons/icon-48.png");
  assert.equal(wrapper.get("h2").text(), "Hide files on pull request pages");
  assert.ok(wrapper.find(".p-toggleswitch").exists());
  assert.ok(wrapper.find(".p-inputtext").exists());
  const remove = wrapper.get("li button");
  assert.equal(remove.attributes("aria-label"), "Remove *.stories.*");
  assert.equal(remove.text(), "");
  assert.ok(remove.find(".pi-trash").exists());
  wrapper.unmount();
});

test("empty and duplicate patterns show inline feedback without writing", async () => {
  storage();
  const wrapper = mount(App);
  await flushPromises();
  await wrapper.get("form").trigger("submit");
  assert.equal(wrapper.get("#pattern-feedback").text(), "Enter a pattern.");
  assert.equal(wrapper.get("#file-pattern").attributes("aria-invalid"), "true");
  await wrapper.get("#file-pattern").setValue(" *.spec.ts ");
  assert.equal(wrapper.get("#pattern-feedback").text(), "");
  await wrapper.get("form").trigger("submit");
  assert.equal(wrapper.get("#pattern-feedback").text(), "This pattern is already in the list.");
  assert.equal(set.mock.calls.length, 0);
  await wrapper.get("#file-pattern").setValue("*.test.ts");
  await wrapper.get("form").trigger("submit");
  await flushPromises();
  assert.equal(wrapper.get("#pattern-feedback").text(), "");
  assert.equal(wrapper.findAll("li").length, 4);
  wrapper.unmount();
});

test("pattern rows look inactive when filtering is off but remain editable", async () => {
  storage();
  const wrapper = mount(App);
  await flushPromises();
  assert.ok(wrapper.get(".settings-patterns").classes().includes("patterns-inactive"));
  assert.equal(wrapper.get("li button").attributes("disabled"), undefined);
  await wrapper.get<HTMLInputElement>('input[type="checkbox"]').setValue(true);
  assert.equal(wrapper.get(".settings-patterns").classes().includes("patterns-inactive"), false);
  wrapper.unmount();
});
