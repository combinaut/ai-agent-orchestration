// @vitest-environment happy-dom
import assert from "node:assert/strict";
import { flushPromises } from "@vue/test-utils";
import Select from "primevue/select";
import { afterEach, test, vi } from "vitest";
import type { RepoName, Replies, Request, Rules, Summary } from "#/types";
import type { ActionCase, FakeReplies, PendingOpeningReplies } from "#test/types";
import { effectiveRepos, pickedRepo, showRepoPicker } from "#/core";
import App from "#popup/App.vue";
import { mountPopup } from "#test/components/mountPopup";
import { createI18n } from "#test/i18n/catalog";

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

function noop(): void {}

const SUMMARY: Summary = {
  prs: 2,
  groups: [{ id: "1", title: "Finder", color: "yellow", count: 2, prs: [] }],
};

async function popup(
  repos: RepoName[],
  replies: FakeReplies = {},
  pending: PendingOpeningReplies = {},
) {
  const rules: Rules = { org: "acme", defaultRepo: "", openAll: "", repos };
  const choices = {
    repos: effectiveRepos(rules),
    selected: pickedRepo(rules),
    show: showRepoPicker(rules),
  };
  const answers: FakeReplies = { repos: choices, summary: SUMMARY, ...replies };
  const sent: Request[] = [];

  async function handleSendMessage(message: Request): Promise<unknown> {
    sent.push(message);

    const held =
      message.type === "openAll" || message.type === "openReviews"
        ? pending[message.type]
        : undefined;

    return held ?? answers[message.type] ?? { error: "unexpected" };
  }

  vi.stubGlobal("chrome", {
    runtime: { sendMessage: handleSendMessage },
    i18n: createI18n(),
  });

  const wrapper = mountPopup(App, { attachTo: document.body });

  await flushPromises();
  sent.length = 0;

  return { wrapper, sent, answers };
}

const ACTIONS: readonly ActionCase[] = [
  {
    button: "#open-all",
    request: { type: "openAll" },
    reply: { opened: 1, remaining: 0, estimated: false },
    status: "Opened 1. 2 PRs in 1 group",
  },
  {
    button: "#open-reviews",
    request: { type: "openReviews" },
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

test("popup content is the capped stable-gutter scroller", async () => {
  const { wrapper } = await popup([]);
  const content = wrapper.find("#popup-content");

  assert.equal(content.element.tagName, "MAIN");
  assert.equal(content.classes().includes("scrollbar-gutter-both-edges"), true);
  assert.equal(content.classes().includes("max-h-popup-content"), true);
  assert.equal(content.classes().includes("overflow-y-auto"), true);

  wrapper.unmount();
});

const PANE_HEAD = { repo: "acme/webapp", branch: "feature" };
const PANE_SUMMARY: Summary = {
  prs: 1,
  groups: [
    {
      id: "2",
      title: "A",
      count: 1,
      prs: [{ key: "acme/webapp#1", label: "webapp#1", title: "", head: PANE_HEAD }],
    },
    {
      id: "3",
      title: "B",
      count: 1,
      prs: [{ key: "acme/webapp#2", label: "webapp#2", title: "", head: PANE_HEAD }],
    },
  ],
};

test("the first expanded group looks up panes once; go to pane focuses and closes", async () => {
  const close = vi.spyOn(window, "close").mockImplementation(noop);
  const { wrapper, sent } = await popup([], {
    summary: PANE_SUMMARY,
    panes: { "acme/webapp:feature": { state: "waiting" } },
    focusPane: { pane: "w1:p2" },
  });

  for (const header of wrapper.findAll("button[aria-expanded]")) {
    await header.trigger("click");
    await flushPromises();
  }

  assert.deepEqual(sent, [{ type: "panes", heads: [PANE_HEAD] }]);
  assert.equal(wrapper.findAll(".p-tag").length, 2);

  await wrapper.find("li .p-button").trigger("click");
  await flushPromises();

  assert.deepEqual(sent.at(-1), { type: "focusPane", head: PANE_HEAD });
  assert.equal(close.mock.calls.length, 1);

  wrapper.unmount();
});

test("reopening the popup displays the active batch and disables both opening actions", async () => {
  const { wrapper } = await popup([], {
    openingProgress: { active: true, opened: 3, planned: 10 },
  });

  assert.equal(wrapper.find("[role=progressbar]").attributes("aria-valuenow"), "30");
  assert.equal(wrapper.find("#opening-progress [aria-live=polite]").text(), "Opened 3 of 10");
  assert.equal(wrapper.find("#open-all").attributes("disabled"), "");
  assert.equal(wrapper.find("#open-reviews").attributes("disabled"), "");
  wrapper.unmount();
});

test("actions use a two-column grid with opening actions before closing actions", async () => {
  const { wrapper } = await popup([]);
  const row = wrapper.find("#open-all").element.parentElement;

  assert.ok(row);
  assert.ok(row.classList.contains("grid"));
  assert.ok(row.classList.contains("grid-cols-2"));
  assert.deepEqual(
    Array.from(row.querySelectorAll("button"), (button) => button.id),
    ["open-all", "open-reviews", "check-now", "close-duplicates"],
  );
  wrapper.unmount();
});

for (const result of [{ opened: 3, remaining: 0, estimated: false }, { error: "offline" }]) {
  test(`an observed batch refreshes on completion and reports ${JSON.stringify(result)}`, async () => {
    vi.useFakeTimers();
    const { wrapper, answers, sent } = await popup([], {
      openingProgress: { active: true, opened: 1, planned: 3 },
    });

    try {
      answers.summary = {
        prs: 3,
        groups: [{ id: "4", title: "New", color: "blue", count: 3, prs: [] }],
      };
      answers.openingProgress = { active: false, opened: 0, planned: 0, result };
      await vi.advanceTimersByTimeAsync(250);
      await flushPromises();

      assert.equal(wrapper.find("[role=progressbar]").exists(), false);
      assert.ok(wrapper.text().includes("New"));
      assert.equal(
        wrapper.find("#status").text(),
        "error" in result ? "offline" : "Opened 3. 3 PRs in 1 group",
      );
      await vi.advanceTimersByTimeAsync(500);
      assert.equal(sent.filter((message) => message.type === "summary").length, 1);
    } finally {
      wrapper.unmount();
      vi.useRealTimers();
    }
  });
}

test("progress refreshes counts and groups before an observed batch finishes", async () => {
  vi.useFakeTimers();
  const { wrapper, answers, sent } = await popup([], {
    openingProgress: { active: true, opened: 0, planned: 3 },
  });

  try {
    answers.summary = {
      prs: 3,
      groups: [{ id: "5", title: "During batch", color: "blue", count: 3, prs: [] }],
    };
    answers.openingProgress = { active: true, opened: 1, planned: 3 };
    await vi.advanceTimersByTimeAsync(250);
    await flushPromises();

    assert.ok(wrapper.text().includes("During batch"));
    assert.equal(wrapper.find("#status").text(), "3 PRs in 1 group");
    assert.equal(wrapper.find("[role=progressbar]").exists(), true);
    assert.equal(sent.filter((message) => message.type === "summary").length, 1);

    await vi.advanceTimersByTimeAsync(500);
    assert.equal(sent.filter((message) => message.type === "summary").length, 1);
  } finally {
    wrapper.unmount();
    vi.useRealTimers();
  }
});

for (const type of ["openAll", "openReviews"] as const) {
  test(`${type} shows searching before the next progress poll`, async () => {
    vi.useFakeTimers();
    const batch = Promise.withResolvers<Replies["openAll"]>();
    const { wrapper } = await popup([], {}, { [type]: batch.promise });
    const selector = type === "openAll" ? "#open-all" : "#open-reviews";

    try {
      await wrapper.find(selector).trigger("click");
      assert.equal(wrapper.find(".p-progressbar-indeterminate").exists(), true);
      assert.equal(wrapper.find("#opening-progress [aria-live=polite]").text(), "Searching…");
      batch.resolve({ opened: 0, remaining: 0, estimated: false });
      await flushPromises();
      assert.equal(wrapper.find("[role=progressbar]").exists(), false);
    } finally {
      batch.resolve({ opened: 0, remaining: 0, estimated: false });
      wrapper.unmount();
      vi.useRealTimers();
    }
  });

  test(`${type} keeps the last counts until its local action settles`, async () => {
    vi.useFakeTimers();
    const batch = Promise.withResolvers<Replies["openAll"]>();
    const { wrapper, answers } = await popup([], {}, { [type]: batch.promise });
    const selector = type === "openAll" ? "#open-all" : "#open-reviews";

    try {
      await wrapper.find(selector).trigger("click");
      answers.openingProgress = { active: true, opened: 2, planned: 3 };
      await vi.advanceTimersByTimeAsync(250);
      answers.openingProgress = { active: false, opened: 0, planned: 0 };
      await vi.advanceTimersByTimeAsync(250);
      assert.equal(wrapper.find("#opening-progress [aria-live=polite]").text(), "Opened 2 of 3");
      batch.resolve({ opened: 2, remaining: 0, estimated: false });
      await flushPromises();
      assert.equal(wrapper.find("[role=progressbar]").exists(), false);
    } finally {
      batch.resolve({ opened: 2, remaining: 0, estimated: false });
      wrapper.unmount();
      vi.useRealTimers();
    }
  });
}

test("a terminal focus failure is visible in the popup error status", async () => {
  const head = { repo: "acme/webapp", branch: "feature" };
  const summary: Summary = {
    prs: 1,
    groups: [
      {
        id: "6",
        title: "A",
        count: 1,
        prs: [{ key: "acme/webapp#1", label: "webapp#1", title: "", head }],
      },
    ],
  };
  const { wrapper, sent } = await popup([], {
    summary,
    panes: { "acme/webapp:feature": { state: "working" } },
    focusPane: { error: "Native focus failed: diagnostic reason" },
  });

  await wrapper.find("button[aria-expanded]").trigger("click");
  await flushPromises();
  await wrapper.find("li .p-button").trigger("click");
  await flushPromises();

  assert.deepEqual(sent.at(-1), { type: "focusPane", head });
  assert.equal(
    wrapper.find("#status .p-message-error").text(),
    "Native focus failed: diagnostic reason",
  );
  assert.equal(wrapper.find("#status .p-message-error").isVisible(), true);

  wrapper.unmount();
});

test("selecting a row focuses its existing tab and closes the popup", async () => {
  const close = vi.spyOn(window, "close").mockImplementation(noop);
  const { wrapper, sent } = await popup([], {
    summary: PANE_SUMMARY,
    panes: {},
    focusPr: { focused: "acme/webapp#1" },
  });
  try {
    await wrapper.find("button[aria-expanded]").trigger("click");
    await flushPromises();
    sent.length = 0;
    await wrapper.find("button[data-pr-tab]").trigger("click");
    await flushPromises();
    assert.deepEqual(sent, [{ type: "focusPr", key: "acme/webapp#1" }]);
    assert.equal(close.mock.calls.length, 1);
  } finally {
    wrapper.unmount();
  }
});

test("hydration settlement refreshes cached titles once without a batch", async () => {
  vi.useFakeTimers();
  const row = {
    key: "acme/webapp#7" as const,
    label: "webapp#7",
    title: "Cached title",
    head: null,
  };
  const group = { id: "10", title: "Finder", count: 1, prs: [row] };
  const { wrapper, answers, sent } = await popup([], { summary: { prs: 1, groups: [group] } });
  try {
    await wrapper.find("button[aria-expanded]").trigger("click");
    assert.ok(wrapper.text().includes("Cached title"));
    answers.summary = {
      prs: 1,
      groups: [{ ...group, prs: [{ ...row, title: "Restored title" }] }],
    };
    answers.openingProgress = { active: false, opened: 0, planned: 0, metadataRevision: 1 };
    await vi.advanceTimersByTimeAsync(250);
    await flushPromises();
    assert.ok(wrapper.text().includes("Restored title"));
    await vi.advanceTimersByTimeAsync(500);
    assert.equal(sent.filter((message) => message.type === "summary").length, 1);
  } finally {
    wrapper.unmount();
    vi.useRealTimers();
  }
});

const SEARCH_SUMMARY: Summary = {
  prs: 2,
  groups: [
    {
      id: "10",
      title: "Finder",
      count: 1,
      prs: [{ key: "acme/webapp#7", label: "webapp#7", title: "Amber Zeppelin", head: null }],
    },
    {
      id: "9",
      title: "Other",
      count: 1,
      prs: [{ key: "acme/webapp#8", label: "webapp#8", title: "Plain record", head: null }],
    },
  ],
};

async function searchablePopup() {
  return popup([], {
    summary: SEARCH_SUMMARY,
    panes: {},
    focusPr: { focused: "acme/webapp#7" },
    closePr: { closed: 1 },
  });
}

test("search expands matches, restores expansion, moves down and Enter focuses the best existing tab", async () => {
  const { wrapper, sent } = await searchablePopup();
  try {
    assert.equal(document.activeElement?.id, "target");
    await wrapper.findAll("button[aria-expanded]")[1]?.trigger("click");
    const search = wrapper.find<HTMLInputElement>("#pr-search");
    await search.setValue("Amber");
    await flushPromises();
    assert.equal(wrapper.findAll("button[aria-expanded]").length, 1);
    assert.equal(wrapper.find("button[aria-expanded]").attributes("aria-expanded"), "true");
    await search.trigger("keydown", { key: "ArrowDown" });
    assert.equal(document.activeElement?.getAttribute("aria-label"), "webapp#7 Amber Zeppelin");
    await search.trigger("keydown", { key: "Escape" });
    await flushPromises();
    assert.equal(wrapper.findAll("button[aria-expanded]")[1]?.attributes("aria-expanded"), "true");
    await search.setValue("Amber");
    await search.trigger("keydown", { key: "Enter" });
    await flushPromises();
    assert.deepEqual(
      sent.filter((message) => message.type === "focusPr" || message.type === "goTo"),
      [{ type: "focusPr", key: "acme/webapp#7" }],
    );
  } finally {
    wrapper.unmount();
  }
});

test("closing the only filtered match refreshes counts and focuses search without closing the popup", async () => {
  const closeWindow = vi.spyOn(window, "close").mockImplementation(noop);
  const { wrapper, answers, sent } = await searchablePopup();
  try {
    await wrapper.find("#pr-search").setValue("Amber");
    await flushPromises();
    answers.summary = { prs: 1, groups: SEARCH_SUMMARY.groups.slice(1) };
    await wrapper.find("button[data-pr-close]").trigger("click");
    await flushPromises();
    assert.equal(wrapper.findAll("button[aria-expanded]").length, 0);
    assert.ok(wrapper.text().includes("No matching PRs"));
    assert.equal(document.activeElement?.id, "pr-search");
    assert.equal(closeWindow.mock.calls.length, 0);
    assert.deepEqual(
      sent.filter((message) => message.type === "closePr" || message.type === "focusPr"),
      [{ type: "closePr", key: "acme/webapp#7" }],
    );
    assert.ok(wrapper.find("#status").text().includes("1 PR"));
  } finally {
    wrapper.unmount();
  }
});
