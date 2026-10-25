// @vitest-environment happy-dom
import assert from "node:assert/strict";
import { flushPromises } from "@vue/test-utils";
import Select from "primevue/select";
import { afterEach, test, vi } from "vitest";
import type { RepoName, Replies, Request, Rules, Summary } from "#/types";
import type { ActionCase } from "#test/types";
import { effectiveRepos, pickedRepo, showRepoPicker } from "#/core";
import App from "#popup/App.vue";
import { mountPopup } from "#test/components/mountPopup";
import { createI18n } from "#test/i18n/catalog";

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

function noop(): void {}

const SUMMARY: Summary = { prs: 2, groups: [{ title: "Finder", color: "yellow", count: 2 }] };

async function popup(repos: RepoName[], replies: Partial<Replies> = {}) {
  const rules: Rules = { org: "acme", defaultRepo: "", openAll: "", repos };
  const choices = {
    repos: effectiveRepos(rules),
    selected: pickedRepo(rules),
    show: showRepoPicker(rules),
  };
  const answers: Partial<Replies> = { repos: choices, summary: SUMMARY, ...replies };
  const sent: Request[] = [];

  async function handleSendMessage(message: Request): Promise<unknown> {
    sent.push(message);

    return answers[message.type] ?? { error: "unexpected" };
  }

  vi.stubGlobal("chrome", {
    runtime: { sendMessage: handleSendMessage },
    i18n: createI18n(),
  });

  const wrapper = mountPopup(App, { attachTo: document.body });

  await flushPromises();
  sent.length = 0;

  return { wrapper, sent };
}

const ACTIONS: readonly ActionCase[] = [
  {
    button: "#open-all",
    request: { type: "openAll" },
    reply: { opened: 1, remaining: 0, estimated: false },
    status: "Opened 1. 2 PRs in 1 group",
  },
  {
    button: "#check-now",
    request: { type: "checkNow" },
    reply: {
      tabs: 1,
      prs: 1,
      checked: 1,
      closed: [{ key: "acme/webapp#1", state: "MERGED" }],
      inView: [],
    },
    status: "Closed 1 merged tab.",
  },
  {
    button: "#close-duplicates",
    request: { type: "closeDuplicates" },
    reply: { closed: 1 },
    status: "Closed 1 duplicate PR tab.",
  },
];

test("each button sends its own request, refreshes the summary and shows the result", async () => {
  for (const action of ACTIONS) {
    const { wrapper, sent } = await popup([], { [action.request.type]: action.reply });

    await wrapper.find(action.button).trigger("click");
    await flushPromises();

    assert.deepEqual(sent, [action.request, { type: "summary" }], action.button);
    assert.equal(wrapper.find("#status").text(), action.status);

    wrapper.unmount();
  }
});

test("submitting the form sends the input with the picked repo and closes the popup", async () => {
  const close = vi.spyOn(window, "close").mockImplementation(noop);
  const { wrapper, sent } = await popup(["webapp", "api"], { goTo: { opened: "acme/api#76" } });

  await wrapper.find("#target").setValue("76");
  wrapper.findComponent(Select).vm.$emit("update:modelValue", "api");
  wrapper.findComponent(Select).vm.$emit("change", { value: "api" });
  await wrapper.find("form").trigger("submit");
  await flushPromises();

  assert.deepEqual(sent, [
    { type: "pickRepo", repo: "api" },
    { type: "goTo", input: "76", repo: "api" },
  ]);
  assert.equal(close.mock.calls.length, 1);

  wrapper.unmount();
});

test("the repo dropdown shows only with two or more repos", async () => {
  for (const repos of [[], ["webapp"], ["webapp", "Webapp"]]) {
    const { wrapper } = await popup(repos);

    await wrapper.find("#target").setValue("76");

    assert.equal(wrapper.findComponent(Select).exists(), false, JSON.stringify(repos));
    assert.equal(wrapper.find("#status").text(), "2 PRs in 1 group");

    wrapper.unmount();
  }

  const { wrapper } = await popup(["webapp", "api"]);

  await wrapper.find("#target").setValue("76");

  const select = wrapper.findComponent(Select);

  assert.equal(select.exists(), true);
  assert.deepEqual(select.props("options"), ["webapp", "api"]);
  assert.equal(select.props("modelValue"), "webapp");

  wrapper.unmount();
});

test("the repo dropdown shows only while the input is a bare PR number", async () => {
  const { wrapper } = await popup(["webapp", "api"]);
  const field = wrapper.find("#target");

  for (const [text, shown] of [
    ["", false],
    ["76", true],
    ["https://github.com/acme/webapp/pull/76", false],
    ["sparkle#76", false],
    ["#76", false],
    ["76a", false],
    [" 76 ", true],
  ] as const) {
    await field.setValue(text);
    assert.equal(wrapper.findComponent(Select).exists(), shown, JSON.stringify(text));
  }

  wrapper.unmount();
});
