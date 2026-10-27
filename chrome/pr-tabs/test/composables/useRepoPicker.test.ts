import assert from "node:assert/strict";
import { ref } from "vue";
import { test } from "vitest";
import { useRepoPicker } from "#/composables/useRepoPicker";
import { fakeAsk } from "./fakeAsk";

test("the picker is available only when the service worker says to show it", async () => {
  const single = useRepoPicker(
    fakeAsk({ repos: { repos: ["webapp"], selected: "webapp", show: false } }).ask,
    "76",
  );

  await single.load();

  assert.equal(single.available.value, false);
  assert.equal(single.visible.value, false);

  const broken = useRepoPicker(fakeAsk({ repos: { error: "No answer" } }).ask, "76");

  await broken.load();

  assert.equal(broken.available.value, false);
});

test("a listed choice selects the stored pick and is visible only for a bare number", async () => {
  const input = ref("");
  const picker = useRepoPicker(
    fakeAsk({ repos: { repos: ["webapp", "api"], selected: "api", show: true } }).ask,
    input,
  );

  await picker.load();

  assert.deepEqual(picker.repos.value, ["webapp", "api"]);
  assert.equal(picker.repo.value, "api");
  assert.equal(picker.available.value, true);

  for (const [text, visible] of [
    ["", false],
    ["76", true],
    [" 76 ", true],
    ["#76", false],
    ["sparkle#76", false],
    ["https://github.com/acme/webapp/pull/76", false],
    ["76a", false],
    ["76, 77", true],
    ["#76\nhttps://github.com/acme/webapp/pull/76 78", true],
    ["#76, sparkle#77", false],
  ] as const) {
    input.value = text;
    assert.equal(picker.visible.value, visible, JSON.stringify(text));
  }
});

test("pick sends the selected repo", async () => {
  const { ask, sent } = fakeAsk({
    repos: { repos: ["webapp", "api"], selected: "webapp", show: true },
    pickRepo: {},
  });

  const picker = useRepoPicker(ask, "");

  await picker.load();
  picker.repo.value = "api";
  picker.pick();

  assert.deepEqual(sent.at(-1), { type: "pickRepo", repo: "api" });
});
