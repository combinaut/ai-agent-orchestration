// @vitest-environment happy-dom
import assert from "node:assert/strict";
import { afterEach, test, vi } from "vitest";
import StatusToast from "#popup/components/StatusToast.vue";
import type { StatusToastProps } from "#/types";
import { mountPopup } from "./mountPopup";

afterEach(() => {
  vi.useRealTimers();
});

const SUCCESS: StatusToastProps = {
  text: "Closed 1 merged tab.",
  error: false,
  shown: true,
  revision: 1,
  raised: true,
};

function toast(props: Partial<StatusToastProps> = {}) {
  return mountPopup(StatusToast, { props: { ...SUCCESS, ...props } });
}

test("a success toast is a status with a check icon and hides after 3 s", async () => {
  vi.useFakeTimers();
  const wrapper = toast();

  assert.equal(wrapper.find("#status").attributes("role"), "status");
  assert.ok(wrapper.find(".pi-check-circle").exists());
  assert.ok(wrapper.find("#status").classes().includes("status-toast-raised"));
  assert.equal(wrapper.find(".status-toast-text").text(), "Closed 1 merged tab.");
  await vi.advanceTimersByTimeAsync(2999);
  assert.ok(wrapper.find("#status").exists());
  await vi.advanceTimersByTimeAsync(1);
  assert.equal(wrapper.find("#status").exists(), false);
});

test("an error toast is an alert that stays until closed", async () => {
  vi.useFakeTimers();
  const wrapper = toast({ text: "offline", error: true, raised: false });

  assert.equal(wrapper.find("#status").attributes("role"), "alert");
  assert.ok(wrapper.find(".pi-exclamation-circle").exists());
  await vi.advanceTimersByTimeAsync(10_000);
  assert.ok(wrapper.find("#status").exists());
  await wrapper.find(".status-toast-close").trigger("click");
  assert.equal(wrapper.find("#status").exists(), false);
});

test("the next message replaces a closed or shown toast; hidden status shows none", async () => {
  const wrapper = toast({ error: true });

  await wrapper.find(".status-toast-close").trigger("click");
  await wrapper.setProps({ revision: 2, text: "Opened 1.", error: false });
  assert.equal(wrapper.find(".status-toast-text").text(), "Opened 1.");
  await wrapper.setProps({ shown: false });
  assert.equal(wrapper.find("#status").exists(), false);
});

test("the close button is labelled", () => {
  assert.equal(toast().find(".status-toast-close").attributes("aria-label"), "Dismiss message");
});
