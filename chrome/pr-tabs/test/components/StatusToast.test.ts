// @vitest-environment happy-dom
import assert from "node:assert/strict";
import { afterEach, test, vi } from "vitest";
import StatusToast from "#popup/components/StatusToast.vue";
import type { StatusKind, StatusToastProps } from "#/types";
import { mountPopup } from "./mountPopup";

afterEach(() => {
  vi.useRealTimers();
});

const SUCCESS: StatusToastProps = {
  text: "Closed 1 merged tab.",
  kind: "success",
  shown: true,
  revision: 1,
  raised: true,
};

function toast(props: Partial<StatusToastProps> = {}) {
  return mountPopup(StatusToast, { props: { ...SUCCESS, ...props } });
}

const KINDS: ReadonlyArray<[StatusKind, string, string]> = [
  ["success", "status", "pi-check-circle"],
  ["info", "status", "pi-info-circle"],
  ["warning", "alert", "pi-exclamation-triangle"],
  ["error", "alert", "pi-times-circle"],
];

test.each(KINDS)("a %s toast is a %s with its own icon", (kind, role, icon) => {
  const status = toast({ kind }).find("#status");

  assert.equal(status.attributes("role"), role);
  assert.ok(status.classes().includes(`status-toast-${kind}`));
  assert.ok(status.find(`.${icon}`).exists());
});

test.each(["success", "info"] as const)("a %s toast hides after 4 s", async (kind) => {
  vi.useFakeTimers();
  const wrapper = toast({ kind });

  assert.ok(wrapper.find("#status").classes().includes("status-toast-raised"));
  assert.equal(wrapper.find(".status-toast-text").text(), "Closed 1 merged tab.");
  await vi.advanceTimersByTimeAsync(3999);
  assert.ok(wrapper.find("#status").exists());
  await vi.advanceTimersByTimeAsync(1);
  assert.equal(wrapper.find("#status").exists(), false);
});

test("hover or focus pauses the hide timer", async () => {
  vi.useFakeTimers();
  const wrapper = toast({ kind: "info" });
  const status = wrapper.find("#status");

  await status.trigger("mouseenter");
  await status.trigger("focusin");
  await vi.advanceTimersByTimeAsync(10_000);
  assert.ok(wrapper.find("#status").exists());
  await status.trigger("mouseleave");
  await vi.advanceTimersByTimeAsync(10_000);
  assert.ok(wrapper.find("#status").exists());
  await status.trigger("focusout");
  await vi.advanceTimersByTimeAsync(4000);
  assert.equal(wrapper.find("#status").exists(), false);
});

test.each(["warning", "error"] as const)("a %s toast stays until closed", async (kind) => {
  vi.useFakeTimers();
  const wrapper = toast({ text: "offline", kind, raised: false });

  await vi.advanceTimersByTimeAsync(10_000);
  assert.ok(wrapper.find("#status").exists());
  await wrapper.find(".status-toast-close").trigger("click");
  assert.equal(wrapper.find("#status").exists(), false);
});

test("the next message replaces a closed or shown toast; hidden status shows none", async () => {
  const wrapper = toast({ kind: "error" });

  await wrapper.find(".status-toast-close").trigger("click");
  await wrapper.setProps({ revision: 2, text: "Opened 1.", kind: "success" });
  assert.equal(wrapper.find(".status-toast-text").text(), "Opened 1.");
  await wrapper.setProps({ shown: false });
  assert.equal(wrapper.find("#status").exists(), false);
});

test("the close button is labelled", () => {
  assert.equal(toast().find(".status-toast-close").attributes("aria-label"), "Dismiss");
});
