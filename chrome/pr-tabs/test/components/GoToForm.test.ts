// @vitest-environment happy-dom
import assert from "node:assert/strict";
import { test } from "vitest";
import GoToForm from "../../src/popup/components/GoToForm.vue";
import RepoSelect from "../../src/popup/components/RepoSelect.vue";
import { mountPopup } from "./mountPopup";

function form(showRepo: boolean) {
  return mountPopup(GoToForm, {
    props: { busy: false, repos: ["webapp", "api"], showRepo, input: "76", repo: "webapp" },
  });
}

test("Enter submits the form as go", async () => {
  const wrapper = form(false);
  assert.match(wrapper.find("label").text(), /Go to a PR/);
  await wrapper.find("form").trigger("submit");
  assert.equal(wrapper.emitted("go")?.length, 1);
});

test("the repo dropdown sits below the input only when shown, and passes picks on", async () => {
  assert.equal(form(false).findComponent(RepoSelect).exists(), false);
  const wrapper = form(true);
  const fields = wrapper.findAll("#target, #repo").map((field) => field.attributes("id"));
  assert.deepEqual(fields, ["target", "repo"]);
  wrapper.findComponent(RepoSelect).vm.$emit("pick");
  assert.equal(wrapper.emitted("pick")?.length, 1);
});

test("typing updates the input model", async () => {
  const wrapper = form(false);
  await wrapper.find("#target").setValue("77");
  assert.deepEqual(wrapper.emitted("update:input")?.at(-1), ["77"]);
});
