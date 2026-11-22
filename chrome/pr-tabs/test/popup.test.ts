// @vitest-environment happy-dom
import assert from "node:assert/strict";
import { flushPromises } from "@vue/test-utils";
import Select from "primevue/select";
import { afterEach, test, vi } from "vitest";
import type { RepoName, Replies, Request, Rules, Summary } from "#/types";
import type { ActionCase, FakeReplies, PendingOpeningReplies } from "#test/types";
import { effectiveRepos, pickedRepo, showRepoPicker } from "#/core";
import App from "#popup/App.vue";
import RepoSelect from "#popup/components/RepoSelect.vue";
import { mountPopup, popupStubs } from "#test/components/mountPopup";
import { createI18n } from "#test/i18n/catalog";

const mounted: { unmount: () => void }[] = [];

afterEach(() => {
  for (const wrapper of mounted.splice(0)) {
    wrapper.unmount();
  }

  vi.useRealTimers();
  vi.unstubAllGlobals();
  document.body.replaceChildren();
});

function noop(): void {}

function withoutPolls(sent: Request[]): Request[] {
  return sent.filter((message) => message.type !== "openingProgress");
}

async function runMenuAction(wrapper: ReturnType<typeof mountPopup>, label: string): Promise<void> {
  await wrapper.find("#actions-menu").trigger("click");
  await flushPromises();
  const item = menuItem(label);

  assert.ok(item, label);
  item.click();
  await flushPromises();
}

function menuItem(label: string): HTMLElement | undefined {
  return [...document.body.querySelectorAll<HTMLElement>(".p-menu-item-link")].find(
    (element) => element.textContent?.trim() === label,
  );
}

const SUMMARY: Summary = {
  prs: 2,
  groups: [{ id: "1", title: "Finder", color: "yellow", count: 2, prs: [] }],
};

function choicesFor(repos: RepoName[]) {
  const rules: Rules = { org: "acme", defaultRepo: "", openAll: "", repos };

  return {
    repos: effectiveRepos(rules),
    selected: pickedRepo(rules),
    show: showRepoPicker(rules),
    newest: {},
  };
}

async function popup(
  repos: RepoName[],
  replies: FakeReplies = {},
  pending: PendingOpeningReplies = {},
) {
  const answers: FakeReplies = { repos: choicesFor(repos), summary: SUMMARY, ...replies };
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

  const wrapper = mountPopup(App, {
    attachTo: document.body,
    global: { stubs: popupStubs },
  });
  mounted.push(wrapper);

  await flushPromises();
  sent.length = 0;

  return { wrapper, sent, answers };
}

const ACTIONS: readonly ActionCase[] = [
  {
    button: "#open-all",
    label: "Open all",
    request: { type: "openAll" },
    reply: { opened: 1, remaining: 0, estimated: false },
    status: "Opened 1. 2 PRs in 1 group",
  },
  {
    button: "#open-reviews",
    label: "Open reviews",
    request: { type: "openReviews" },
    reply: { opened: 1, remaining: 0, estimated: false },
    status: "Opened 1. 2 PRs in 1 group",
  },
  {
    button: "#check-now",
    label: "Close merged",
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
    label: "Close duplicates",
    request: { type: "closeDuplicates" },
    reply: { closed: 1 },
    status: "Closed 1 duplicate PR tab.",
  },
];

test("each button sends its own request, refreshes the summary and shows the result", async () => {
  vi.useFakeTimers();

  try {
    for (const action of ACTIONS) {
      const { wrapper, sent } = await popup([], { [action.request.type]: action.reply });

      await runMenuAction(wrapper, action.label);

      assert.deepEqual(sent, [action.request, { type: "summary" }], action.button);
      assert.equal(wrapper.find("#status").text(), action.status);

      wrapper.unmount();
    }
  } finally {
    vi.useRealTimers();
  }
});

test("a named repo opens directly with the picked repo and closes the popup", async () => {
  const close = vi.spyOn(window, "close").mockImplementation(noop);
  const { wrapper, sent } = await popup(["webapp", "api"], { goTo: { opened: "acme/api#76" } });

  await wrapper.find("#target").setValue("api#76");
  await wrapper.find("form").trigger("submit");
  await flushPromises();

  assert.deepEqual(withoutPolls(sent), [{ type: "goTo", input: "api#76", repo: "webapp" }]);
  assert.equal(close.mock.calls.length, 1);

  wrapper.unmount();
});

test("typing a bare number keeps focus in the input and leaves the dropdown closed", async () => {
  for (const repos of [[], ["webapp"], ["webapp", "Webapp"]]) {
    const { wrapper } = await popup(repos);

    await wrapper.find("#target").setValue("76");

    assert.equal(wrapper.findComponent(Select).exists(), false, JSON.stringify(repos));
    assert.equal(wrapper.find("#status").text(), "2 PRs in 1 group");

    wrapper.unmount();
  }

  const { wrapper } = await popup(["webapp", "api"]);
  const field = wrapper.find("#target");

  await field.setValue("505");

  assert.equal(document.activeElement, field.element);
  assert.equal(wrapper.findComponent(Select).exists(), false);

  wrapper.unmount();
});

test("Enter opens a preselected bare-number dropdown and the next Enter opens the PR", async () => {
  const close = vi.spyOn(window, "close").mockImplementation(noop);
  const { wrapper, sent } = await popup(["webapp", "api", "docs"], {
    repos: {
      repos: ["webapp", "api", "docs"],
      selected: "api",
      show: true,
      newest: { webapp: 520, api: 2000, docs: 34400 },
    },
    goTo: { opened: "acme/webapp#505" },
  });

  await wrapper.find("#target").setValue("505");
  await wrapper.find("form").trigger("submit");
  await flushPromises();

  assert.equal(wrapper.findComponent(Select).props("modelValue"), "webapp");
  assert.ok(document.querySelector(".p-select-overlay"));
  assert.deepEqual(withoutPolls(sent), []);
  wrapper.findComponent(RepoSelect).vm.$emit("accept");
  await flushPromises();
  assert.deepEqual(withoutPolls(sent), [{ type: "goTo", input: "505", repo: "webapp" }]);
  assert.equal(close.mock.calls.length, 1);
  wrapper.unmount();
});

test("Enter always opens a bare-number picker before opening its PR", async () => {
  const close = vi.spyOn(window, "close").mockImplementation(noop);
  const { wrapper, sent } = await popup(["infra", "big", "orch", "hub"], {
    repos: {
      repos: ["infra", "big", "orch", "hub"],
      selected: "big",
      show: true,
      newest: { infra: 515, big: 34352, orch: 198, hub: 111 },
    },
    goTo: { opened: "acme/infra#515" },
  });

  await wrapper.find("#target").setValue("515");
  await wrapper.find("form").trigger("submit");
  await flushPromises();
  assert.equal(wrapper.findComponent(Select).props("modelValue"), "infra");
  assert.deepEqual(withoutPolls(sent), []);
  wrapper.findComponent(RepoSelect).vm.$emit("accept");
  await flushPromises();
  assert.deepEqual(withoutPolls(sent), [{ type: "goTo", input: "515", repo: "infra" }]);
  assert.equal(close.mock.calls.length, 1);
  wrapper.unmount();
});

test("Escape keeps the popup open, closes the dropdown, and returns focus to the input", async () => {
  const close = vi.spyOn(window, "close").mockImplementation(noop);
  const { wrapper } = await popup(["webapp", "api"]);
  const field = wrapper.find("#target");

  await field.setValue("76");
  await wrapper.find("form").trigger("submit");
  await flushPromises();
  const event = new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true });
  document.dispatchEvent(event);
  await flushPromises();

  assert.equal(event.defaultPrevented, true);
  assert.equal(wrapper.findComponent(Select).exists(), false);
  assert.equal(document.activeElement, field.element);
  assert.equal(close.mock.calls.length, 0);
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
      prs: [{ tabId: 1, key: "acme/webapp#1", label: "webapp#1", title: "", head: PANE_HEAD }],
    },
    {
      id: "3",
      title: "B",
      count: 1,
      prs: [{ tabId: 2, key: "acme/webapp#2", label: "webapp#2", title: "", head: PANE_HEAD }],
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

  for (const header of wrapper.findAll("button[aria-expanded]:not(#actions-menu)")) {
    await header.trigger("click");
    await flushPromises();
  }

  assert.deepEqual(withoutPolls(sent), [{ type: "panes", heads: [PANE_HEAD] }]);
  assert.equal(wrapper.findAll(".p-tag").length, 2);

  await wrapper.find("li .p-button").trigger("click");
  await flushPromises();

  assert.deepEqual(withoutPolls(sent).at(-1), { type: "focusPane", head: PANE_HEAD });
  assert.equal(close.mock.calls.length, 1);

  wrapper.unmount();
});

test("reopening the popup displays the active batch and disables both opening actions", async () => {
  const { wrapper } = await popup([], {
    openingProgress: { active: true, opened: 3, planned: 10 },
  });

  assert.equal(wrapper.find("[role=progressbar]").attributes("aria-valuenow"), "30");
  assert.equal(wrapper.find("#opening-progress [aria-live=polite]").text(), "Opened 3 of 10");
  await wrapper.find("#actions-menu").trigger("click");
  await flushPromises();
  assert.equal(document.body.textContent?.includes("Open all"), true);
  assert.equal(document.body.textContent?.includes("Open reviews"), true);
  wrapper.unmount();
});

test("actions live in the burger menu", async () => {
  const { wrapper } = await popup([]);
  assert.equal(wrapper.find("#actions-menu").attributes("aria-expanded"), "false");
  assert.ok(wrapper.find("#actions-menu svg").exists());
  await wrapper.find("#actions-menu").trigger("click");
  await flushPromises();
  assert.equal(wrapper.find("#actions-menu").attributes("aria-expanded"), "true");
  const items = [...document.body.querySelectorAll<HTMLElement>(".p-menu-item-link")];
  assert.deepEqual(
    items.map((item) => item.textContent?.trim()),
    ["Open all", "Open reviews", "Close merged", "Close duplicates"],
  );
  assert.equal(
    items.every((item) => item.querySelector("svg") !== null),
    true,
  );
  wrapper.unmount();
});

test("action menu keeps the header geometry fixed and opens below its trigger", async () => {
  const { wrapper } = await popup([]);
  const header = wrapper.find("#go > div");
  const input = wrapper.find("#target");
  const trigger = wrapper.find<HTMLButtonElement>("#actions-menu");
  const headerClasses = header.attributes("class");
  const inputClasses = input.attributes("class");

  assert.ok(header.classes().includes("h-6"));
  assert.ok(trigger.classes().includes("size-6"));
  assert.ok(trigger.classes().includes("p-0"));
  await trigger.trigger("click");
  await flushPromises();

  assert.equal(header.attributes("class"), headerClasses);
  assert.equal(input.attributes("class"), inputClasses);
  wrapper.unmount();
});

test("close actions stay visible but disabled without PR tabs", async () => {
  const { wrapper, sent } = await popup([], { summary: { prs: 0, groups: [] } });
  await wrapper.find("#actions-menu").trigger("click");
  await flushPromises();

  const closeMerged = menuItem("Close merged");
  const closeDuplicates = menuItem("Close duplicates");
  assert.equal(closeMerged?.getAttribute("aria-disabled"), "true");
  assert.equal(closeDuplicates?.getAttribute("aria-disabled"), "true");
  closeMerged?.click();
  await flushPromises();
  assert.deepEqual(sent, []);
  wrapper.unmount();
});

test("close actions are enabled when PR tabs exist", async () => {
  const { wrapper } = await popup([]);
  await wrapper.find("#actions-menu").trigger("click");
  await flushPromises();

  assert.equal(menuItem("Close merged")?.getAttribute("aria-disabled"), "false");
  assert.equal(menuItem("Close duplicates")?.getAttribute("aria-disabled"), "false");
  wrapper.unmount();
});

test("search is hidden without PR tabs and shown when a PR tab exists", async () => {
  const empty = await popup([], { summary: { prs: 0, groups: [] } });
  assert.ok(empty.wrapper.find("#popup-content").classes().includes("p-popup-padding"));
  assert.equal(empty.wrapper.find("#pr-search").exists(), false);
  assert.equal(empty.wrapper.find("#groups").exists(), false);
  assert.ok(empty.wrapper.text().includes("No PR tabs open"));
  empty.wrapper.unmount();

  const populated = await popup([]);
  assert.ok(populated.wrapper.find("#popup-content").classes().includes("p-popup-padding"));
  assert.equal(populated.wrapper.find("#pr-search").exists(), true);
  assert.equal(populated.wrapper.find("#groups").exists(), true);
  populated.wrapper.unmount();
});

test("the popup root keeps the menu reservation with empty, short, and long lists", async () => {
  for (const summary of [
    { prs: 0, groups: [] },
    SUMMARY,
    {
      prs: 9,
      groups: Array.from({ length: 9 }, (_, id) => ({
        id: String(id),
        title: "A",
        count: 1,
        prs: [],
      })),
    },
  ]) {
    const { wrapper } = await popup([], { summary });
    assert.ok(wrapper.find("#popup-content").exists());
    wrapper.unmount();
  }
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
    const label = type === "openAll" ? "Open all" : "Open reviews";

    try {
      await runMenuAction(wrapper, label);
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
    const label = type === "openAll" ? "Open all" : "Open reviews";

    try {
      await runMenuAction(wrapper, label);
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
        prs: [{ tabId: 1, key: "acme/webapp#1", label: "webapp#1", title: "", head }],
      },
    ],
  };
  const { wrapper, sent } = await popup([], {
    summary,
    panes: { "acme/webapp:feature": { state: "working" } },
    focusPane: { error: "Native focus failed: diagnostic reason" },
  });

  await wrapper.find("button[aria-expanded]:not(#actions-menu)").trigger("click");
  await flushPromises();
  await wrapper.find("li .p-button").trigger("click");
  await flushPromises();

  assert.deepEqual(withoutPolls(sent).at(-1), { type: "focusPane", head });
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
    await wrapper.find("button[aria-expanded]:not(#actions-menu)").trigger("click");
    await flushPromises();
    sent.length = 0;
    await wrapper.find("button[data-pr-tab]").trigger("click");
    await flushPromises();
    assert.deepEqual(withoutPolls(sent), [{ type: "focusPr", key: "acme/webapp#1" }]);
    assert.equal(close.mock.calls.length, 1);
  } finally {
    wrapper.unmount();
  }
});

test("hydration settlement refreshes cached titles once without a batch", async () => {
  vi.useFakeTimers();
  const row = {
    tabId: 7,
    key: "acme/webapp#7" as const,
    label: "webapp#7",
    title: "Cached title",
    head: null,
  };
  const group = { id: "10", title: "Finder", count: 1, prs: [row] };
  const { wrapper, answers, sent } = await popup([], { summary: { prs: 1, groups: [group] } });
  try {
    await wrapper.find("button[aria-expanded]:not(#actions-menu)").trigger("click");
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
      prs: [
        { tabId: 7, key: "acme/webapp#7", label: "webapp#7", title: "Amber Zeppelin", head: null },
      ],
    },
    {
      id: "9",
      title: "Other",
      count: 1,
      prs: [
        { tabId: 8, key: "acme/webapp#8", label: "webapp#8", title: "Plain record", head: null },
      ],
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
    await wrapper.findAll("button[aria-expanded]:not(#actions-menu)")[1]?.trigger("click");
    const search = wrapper.find<HTMLInputElement>("#pr-search");
    await search.setValue("Amber");
    await flushPromises();
    assert.equal(wrapper.findAll("button[aria-expanded]:not(#actions-menu)").length, 1);
    assert.equal(
      wrapper.find("button[aria-expanded]:not(#actions-menu)").attributes("aria-expanded"),
      "true",
    );
    await search.trigger("keydown", { key: "ArrowDown" });
    assert.equal(document.activeElement?.getAttribute("aria-label"), "webapp#7 Amber Zeppelin");
    await search.trigger("keydown", { key: "Escape" });
    await flushPromises();
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
    assert.equal(wrapper.findAll("button[aria-expanded]:not(#actions-menu)").length, 0);
    assert.ok(wrapper.text().includes("No matching PRs"));
    assert.equal(document.activeElement?.id, "pr-search");
    assert.equal(closeWindow.mock.calls.length, 0);
    assert.deepEqual(
      sent.filter((message) => message.type === "closePr" || message.type === "focusPr"),
      [{ type: "closePr", key: "acme/webapp#7", tabId: 7 }],
    );
    assert.ok(wrapper.find("#status").text().includes("1 PR"));
  } finally {
    wrapper.unmount();
  }
});
