// @vitest-environment happy-dom
import assert from "node:assert/strict";
import ProgressBar from "primevue/progressbar";
import { test } from "vitest";
import OpeningProgress from "#popup/components/OpeningProgress.vue";
import { mountPopup } from "./mountPopup";

test("opening progress is hidden when idle and while a completed summary is shown", async () => {
  const wrapper = mountPopup(OpeningProgress, {
    props: { progress: { active: false, opened: 0, planned: 0 } },
  });
  assert.equal(wrapper.find("[role=progressbar]").exists(), false);

  await wrapper.setProps({ progress: { active: true, opened: 1, planned: 4 } });
  assert.equal(wrapper.find("[role=progressbar]").attributes("aria-valuenow"), "25");
  assert.equal(wrapper.find("[aria-live=polite]").text(), "Opened 1 of 4");
  assert.equal(wrapper.findComponent(ProgressBar).props("showValue"), false);

  await wrapper.setProps({ progress: { active: false, opened: 0, planned: 0 } });
  assert.equal(wrapper.find("[role=progressbar]").exists(), false);
  wrapper.unmount();
});

test("search progress is indeterminate before the plan is known", () => {
  const wrapper = mountPopup(OpeningProgress, {
    props: { progress: { active: true, opened: 0, planned: 0 } },
  });

  assert.equal(wrapper.findComponent(ProgressBar).props("mode"), "indeterminate");
  assert.equal(wrapper.find("[aria-live=polite]").text(), "Searching…");
  wrapper.unmount();
});
