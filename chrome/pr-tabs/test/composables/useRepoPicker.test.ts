import assert from "node:assert/strict";
import { ref } from "vue";
import { test } from "vitest";
import { useRepoPicker } from "#/composables/useRepoPicker";
import { fakeAsk } from "./fakeAsk";

test("the picker is available only when the service worker says to show it", async () => {
  const single = useRepoPicker(
    fakeAsk({ repos: { repos: ["webapp"], selected: "webapp", show: false, newest: {} } }).ask,
    "76",
  );

  await single.load();

  assert.equal(single.available.value, false);

  const broken = useRepoPicker(fakeAsk({ repos: { error: "No answer" } }).ask, "76");

  await broken.load();

  assert.equal(broken.available.value, false);
});

test("a listed choice selects the stored pick", async () => {
  const input = ref("");
  const picker = useRepoPicker(
    fakeAsk({
      repos: {
        repos: ["webapp", "api"],
        selected: "api",
        show: true,
        newest: { webapp: 520, api: 2000 },
      },
    }).ask,
    input,
  );

  await picker.load();

  assert.deepEqual(picker.repos.value, ["webapp", "api"]);
  assert.equal(picker.repo.value, "api");
  assert.equal(picker.available.value, true);
});

test("a bare number preselects a repo that can hold it without saving it", async () => {
  const input = ref("");
  const { ask, sent } = fakeAsk({
    repos: {
      repos: ["webapp", "api", "docs"],
      selected: "webapp",
      show: true,
      newest: { webapp: 520, api: 2000, docs: 34400 },
    },
  });
  const picker = useRepoPicker(ask, input);

  await picker.load();
  input.value = "505";
  picker.suggest();
  assert.equal(picker.repo.value, "webapp");
  input.value = "1500";
  picker.suggest();
  assert.equal(picker.repo.value, "api");
  input.value = "#34330";
  picker.suggest();
  assert.equal(picker.repo.value, "docs");
  input.value = "99999";
  picker.suggest();
  assert.equal(picker.repo.value, "webapp");
  assert.deepEqual(sent, [{ type: "repos" }]);
});

test("pick sends the selected repo", async () => {
  const { ask, sent } = fakeAsk({
    repos: { repos: ["webapp", "api"], selected: "webapp", show: true, newest: {} },
    pickRepo: {},
  });

  const picker = useRepoPicker(ask, "");

  await picker.load();
  picker.repo.value = "api";
  picker.pick();

  assert.deepEqual(sent.at(-1), { type: "pickRepo", repo: "api" });
});
