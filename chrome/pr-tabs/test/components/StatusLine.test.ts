// @vitest-environment happy-dom
import assert from "node:assert/strict";
import Message from "primevue/message";
import { test } from "vitest";
import StatusLine from "#popup/components/StatusLine.vue";
import { mountPopup } from "./mountPopup";

test("plain text shows as muted text, an error as an error message", () => {
  const info = mountPopup(StatusLine, { props: { text: "2 PRs in 1 group", error: false } });

  assert.equal(info.findComponent(Message).exists(), false);
  assert.equal(info.find("p").text(), "2 PRs in 1 group");

  const error = mountPopup(StatusLine, { props: { text: "gh: not logged in", error: true } });
  const message = error.findComponent(Message);

  assert.equal(message.props("severity"), "error");
  assert.equal(message.text(), "gh: not logged in");
  assert.equal(error.find("[role=status]").attributes("aria-live"), "polite");
});
