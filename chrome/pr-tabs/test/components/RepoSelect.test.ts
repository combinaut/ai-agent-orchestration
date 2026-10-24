// @vitest-environment happy-dom
import assert from "node:assert/strict";
import Select from "primevue/select";
import { test } from "vitest";
import RepoSelect from "../../src/popup/components/RepoSelect.vue";
import { mountPopup } from "./mountPopup";

test("the dropdown offers the repos with the model selected, and a change is a pick", async () => {
  const wrapper = mountPopup(RepoSelect, {
    props: { repos: ["webapp", "api"], modelValue: "api" },
  });
  const select = wrapper.findComponent(Select);
  assert.deepEqual(select.props("options"), ["webapp", "api"]);
  assert.equal(select.props("modelValue"), "api");
  select.vm.$emit("update:modelValue", "webapp");
  select.vm.$emit("change", { value: "webapp" });
  assert.deepEqual(wrapper.emitted("update:modelValue")?.at(-1), ["webapp"]);
  assert.equal(wrapper.emitted("pick")?.length, 1);
});
