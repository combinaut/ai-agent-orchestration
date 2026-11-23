// @vitest-environment happy-dom
import assert from "node:assert/strict";
import { flushPromises } from "@vue/test-utils";
import { afterEach, test, vi } from "vitest";
import type { RepoName, Replies, Request, Rules, Summary } from "#/types";
import type { ActionCase, FakeReplies, PendingOpeningReplies } from "#test/types";
import { effectiveRepos, pickedRepo, showRepoPicker } from "#/core";
import App from "#popup/App.vue";
import { mountPopup, popupStubs } from "#test/components/mountPopup";
import { createI18n } from "#test/i18n/catalog";

const mounted: { unmount: () => void; vm: { $: { isUnmounted: boolean } } }[] = [];

afterEach(() => {
  for (const wrapper of mounted.splice(0).filter((item) => !item.vm.$.isUnmounted)) {
    wrapper.unmount();
  }

  for (const key of Object.keys(stored)) {
    delete stored[key];
  }

  vi.useRealTimers();
  vi.unstubAllGlobals();
  document.body.replaceChildren();
});

function noop(): void {}

const stored: Record<string, unknown> = {};

function fakeLocalStorage() {
  return {
    async get(key: string): Promise<Record<string, unknown>> {
      return key in stored ? { [key]: stored[key] } : {};
    },
    async set(values: Record<string, unknown>): Promise<void> {
      Object.assign(stored, values);
    },
  };
}

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
    storage: { local: fakeLocalStorage() },
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
    label: "Open all",
    request: { type: "openAll" },
    reply: { opened: 1, remaining: 0, estimated: false },
    status: "Opened 1. 2 PRs in 1 group",
  },
  {
    label: "Open reviews",
    request: { type: "openReviews" },
    reply: { opened: 1, remaining: 0, estimated: false },
    status: "Opened 1. 2 PRs in 1 group",
  },
  {
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

      assert.deepEqual(sent, [action.request, { type: "summary" }], action.label);
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
  await wrapper.find("#target").trigger("keydown", { key: "Enter" });
  await flushPromises();

  assert.deepEqual(withoutPolls(sent), [{ type: "goTo", input: "api#76", repo: "webapp" }]);
  assert.equal(close.mock.calls.length, 1);

  wrapper.unmount();
});

test("the unified field detects a bare number without opening suggestions", async () => {
  for (const repos of [[], ["webapp"], ["webapp", "Webapp"]]) {
    const { wrapper } = await popup(repos);

    await wrapper.find("#target").setValue("76");

    assert.equal(wrapper.find(".repo-option").exists(), false, JSON.stringify(repos));
    assert.equal(wrapper.find("#status").exists(), false);
    assert.ok(wrapper.find("footer").text().endsWith("↵ open · ↑↓ repository · esc close"));

    wrapper.unmount();
  }

  const { wrapper } = await popup(["webapp", "api"]);
  const field = wrapper.find("#target");

  await field.setValue("505");

  assert.equal(document.activeElement, field.element);
  assert.equal(field.attributes("role"), "combobox");
  assert.equal(field.attributes("aria-controls"), "popup-list");
  assert.equal(field.attributes("aria-activedescendant"), undefined);
  assert.ok(wrapper.find("footer").text().endsWith("↵ open · ↑↓ repository · esc close"));

  wrapper.unmount();
});

test("Enter always opens a bare-number picker before opening its PR", async () => {
  const close = vi.spyOn(window, "close").mockImplementation(noop);
  const { wrapper, sent } = await popup(["infra", "big", "orch", "hub"], {
    repos: {
      repos: ["infra", "big", "orch", "hub"],
      selected: "hub",
      show: true,
      newest: { infra: 515, big: 34352, orch: 198, hub: 111 },
    },
    goTo: { opened: "acme/infra#515" },
  });

  await wrapper.find("#target").setValue("515");
  const field = wrapper.find("#target");
  await field.trigger("keydown", { key: "Enter" });
  await flushPromises();
  assert.equal(wrapper.find("[role=option][aria-selected=true]").text(), "infra");
  assert.equal(field.attributes("aria-activedescendant"), "repo-infra");
  assert.equal(wrapper.find(".goto-label").text(), "Open #515 in");
  assert.deepEqual(withoutPolls(sent), []);
  await field.trigger("keydown", { key: "Enter" });
  await flushPromises();
  assert.deepEqual(withoutPolls(sent), [
    { type: "pickRepo", repo: "infra" },
    { type: "goTo", input: "515", repo: "infra" },
  ]);
  assert.equal(close.mock.calls.length, 1);
  wrapper.unmount();
});

test("Escape closes suggestions before clearing the field and keeps the popup open", async () => {
  const close = vi.spyOn(window, "close").mockImplementation(noop);
  const { wrapper } = await popup(["webapp", "api"]);
  const field = wrapper.find("#target");

  await field.setValue("76");
  await field.trigger("keydown", { key: "Enter" });
  await flushPromises();
  const event = new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true });
  field.element.dispatchEvent(event);
  await flushPromises();

  assert.equal(event.defaultPrevented, true);
  assert.equal(wrapper.find(".repo-option").exists(), false);
  assert.equal((field.element as HTMLInputElement).value, "76");
  assert.equal(document.activeElement, field.element);
  await field.trigger("keydown", { key: "Escape" });
  assert.equal((field.element as HTMLInputElement).value, "");
  assert.equal(close.mock.calls.length, 0);
  wrapper.unmount();
});

test("popup content is a frame of title, field, list and footer", async () => {
  const { wrapper } = await popup([]);
  const content = wrapper.find("#popup-content");

  assert.equal(content.element.tagName, "MAIN");
  assert.deepEqual(
    [...content.element.children].map((child) => child.tagName),
    ["HEADER", "DIV", "SECTION", "FOOTER"],
  );
  assert.equal(wrapper.find("#popup-list").attributes("role"), "listbox");

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

test("listed groups look up panes once, also across collapse; go to pane focuses and closes", async () => {
  const close = vi.spyOn(window, "close").mockImplementation(noop);
  const { wrapper, sent } = await popup([], {
    summary: PANE_SUMMARY,
    panes: { "acme/webapp:feature": { state: "waiting" } },
    focusPane: { pane: "w1:p2" },
  });

  for (const header of wrapper.findAll(".group-header")) {
    await header.trigger("click");
    await flushPromises();
    await header.trigger("click");
    await flushPromises();
  }

  assert.deepEqual(withoutPolls(sent), []);
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
  assert.equal(menuItem("Open all")?.getAttribute("aria-disabled"), "true");
  assert.equal(menuItem("Open reviews")?.getAttribute("aria-disabled"), "true");
  wrapper.unmount();
});

test("actions live in the burger menu", async () => {
  const { wrapper } = await popup([]);
  await wrapper.find("#actions-menu").trigger("click");
  await flushPromises();
  const items = [...document.body.querySelectorAll<HTMLElement>(".p-menu-item-link")];
  assert.equal(document.body.querySelectorAll(".p-menu-separator").length, 1);
  assert.deepEqual(
    items.map((item) => item.textContent?.trim()),
    ["Open all", "Open reviews", "Close merged", "Close duplicates"],
  );
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

test("the empty state replaces the list and footer until a PR tab exists", async () => {
  const empty = await popup([], { summary: { prs: 0, groups: [] } });
  assert.equal(empty.wrapper.find("#target").exists(), true);
  assert.equal(empty.wrapper.find("#groups").exists(), false);
  assert.equal(empty.wrapper.find("footer").exists(), false);
  assert.ok(empty.wrapper.text().includes("No PR tabs open"));
  empty.wrapper.unmount();

  const populated = await popup([]);
  assert.equal(populated.wrapper.find("#target").exists(), true);
  assert.equal(populated.wrapper.find("#groups").exists(), true);
  assert.equal(
    populated.wrapper.find("footer").text(),
    "2 PRs in 1 group↑↓ select · ↵ open · esc clear",
  );
  populated.wrapper.unmount();
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
    assert.equal(wrapper.find("#status").exists(), false);
    assert.ok(wrapper.find("footer").text().startsWith("3 PRs in 1 group"));
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

  await wrapper.find("li .p-button").trigger("click");
  await flushPromises();

  assert.deepEqual(withoutPolls(sent).at(-1), { type: "focusPane", head });
  assert.equal(
    wrapper.find("#status.status-toast-error").text(),
    "Native focus failed: diagnostic reason",
  );
  assert.equal(wrapper.find("#status").attributes("role"), "alert");

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

test("search filters, auto-expands matches and selects the first match", async () => {
  const { wrapper } = await searchablePopup();
  try {
    await wrapper.findAll(".group-header")[0]?.trigger("click");
    await flushPromises();
    assert.equal(wrapper.findAll(".group-header")[0]?.attributes("aria-expanded"), "false");
    const search = wrapper.find<HTMLInputElement>("#target");
    await search.setValue("amber");
    await flushPromises();
    assert.equal(wrapper.findAll(".group-header").length, 1);
    assert.equal(wrapper.find(".group-header").attributes("aria-expanded"), "true");
    assert.equal(wrapper.find(".group-badge").text(), "1/1");
    assert.equal(wrapper.find("footer").text(), "1 of 2 PRs↑↓ select · ↵ open · esc clear");
    assert.equal(wrapper.find(".popup-field-trailing").text(), "1 match");
    assert.deepEqual(
      wrapper.findAll("mark").map((mark) => mark.text()),
      ["Amber"],
    );
    assert.equal(search.attributes("aria-activedescendant"), "pr-7");
    assert.equal(document.activeElement, search.element);
  } finally {
    wrapper.unmount();
  }
});

test("Escape clears a search and restores collapse; Enter focuses the selected match", async () => {
  const { wrapper, sent } = await searchablePopup();
  try {
    await wrapper.findAll(".group-header")[0]?.trigger("click");
    const search = wrapper.find<HTMLInputElement>("#target");
    await search.setValue("amber");
    await flushPromises();
    await search.trigger("keydown", { key: "Escape" });
    await flushPromises();
    assert.equal(search.element.value, "");
    assert.equal(wrapper.findAll(".group-header")[0]?.attributes("aria-expanded"), "false");
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

test("arrow keys move the selection across visible rows, skipping collapsed groups", async () => {
  const { wrapper } = await searchablePopup();
  try {
    const field = wrapper.find("#target");

    assert.equal(field.attributes("aria-activedescendant"), undefined);
    await field.trigger("keydown", { key: "ArrowDown" });
    assert.equal(field.attributes("aria-activedescendant"), "pr-7");
    await field.trigger("keydown", { key: "ArrowDown" });
    assert.equal(field.attributes("aria-activedescendant"), "pr-8");
    await field.trigger("keydown", { key: "ArrowDown" });
    assert.equal(field.attributes("aria-activedescendant"), "pr-8");
    await field.trigger("keydown", { key: "ArrowUp" });
    assert.equal(field.attributes("aria-activedescendant"), "pr-7");
    await wrapper.findAll(".group-header")[0]?.trigger("keydown", { key: "ArrowLeft" });
    await flushPromises();
    assert.equal(field.attributes("aria-activedescendant"), "pr-8");
  } finally {
    wrapper.unmount();
  }
});

test("a collapsed group stays collapsed in the next popup", async () => {
  const first = await searchablePopup();
  await first.wrapper.findAll(".group-header")[1]?.trigger("click");
  await flushPromises();
  first.wrapper.unmount();

  const second = await searchablePopup();
  assert.deepEqual(
    second.wrapper.findAll(".group-header").map((header) => header.attributes("aria-expanded")),
    ["true", "false"],
  );
  second.wrapper.unmount();
});

test("an already open PR in the picked repo switches to its tab instead of opening a duplicate", async () => {
  const close = vi.spyOn(window, "close").mockImplementation(noop);
  const { wrapper, sent } = await popup(["webapp", "api"], {
    summary: SEARCH_SUMMARY,
    repos: { repos: ["webapp", "api"], selected: "webapp", show: true, newest: {} },
    focusPr: { focused: "acme/webapp#7" },
  });
  const field = wrapper.find("#target");

  await field.setValue("7");
  await field.trigger("keydown", { key: "Enter" });
  await flushPromises();
  assert.equal(wrapper.find(".repo-option-hint").text(), "already open · ↵ switch");
  assert.equal(wrapper.find(".goto-matching").text(), "Open tabs matching 7");
  assert.deepEqual(
    wrapper.findAll(".pr-number").map((number) => number.text()),
    ["#7"],
  );
  await field.trigger("keydown", { key: "Enter" });
  await flushPromises();
  assert.deepEqual(withoutPolls(sent), [{ type: "focusPr", key: "acme/webapp#7" }]);
  assert.equal(close.mock.calls.length, 1);
  wrapper.unmount();
});

test("Enter on a bare number before the repos load waits and asks for the repo", async () => {
  let release = noop;
  const choices = { repos: ["webapp", "api"], selected: "webapp", show: true, newest: {} };
  const held = new Promise((resolve) => {
    release = () => resolve(choices);
  });
  const { wrapper, sent } = await popup(["webapp", "api"], {
    repos: held as unknown as Replies["repos"],
  });
  const field = wrapper.find("#target");

  await field.setValue("505");
  await field.trigger("keydown", { key: "Enter" });
  await flushPromises();
  assert.deepEqual(withoutPolls(sent), []);
  release();
  await flushPromises();
  assert.equal(wrapper.find("[role=option][aria-selected=true]").text(), "webapp");
  assert.deepEqual(withoutPolls(sent), []);
  wrapper.unmount();
});

async function openRowPopup() {
  vi.spyOn(window, "close").mockImplementation(noop);

  return popup(["webapp", "api"], {
    summary: SEARCH_SUMMARY,
    repos: { repos: ["webapp", "api"], selected: "webapp", show: true, newest: {} },
    focusPr: { focused: "acme/webapp#7" },
  });
}

test("Enter on an arrow-selected open row of a bare number focuses its tab", async () => {
  const { wrapper, sent } = await openRowPopup();
  const field = wrapper.find("#target");

  await field.setValue("7");
  await field.trigger("keydown", { key: "ArrowDown" });
  await field.trigger("keydown", { key: "Enter" });
  await flushPromises();
  assert.deepEqual(withoutPolls(sent), [{ type: "focusPr", key: "acme/webapp#7" }]);
  wrapper.unmount();
});

test("Enter on a hovered open row of a bare number focuses its tab", async () => {
  const { wrapper, sent } = await openRowPopup();
  const field = wrapper.find("#target");

  await field.setValue("7");
  await wrapper.find("#pr-7").trigger("mouseenter");
  await field.trigger("keydown", { key: "Enter" });
  await flushPromises();
  assert.deepEqual(withoutPolls(sent), [{ type: "focusPr", key: "acme/webapp#7" }]);
  wrapper.unmount();
});

test("the repo picker highlights the repo where the number is already open", async () => {
  const { wrapper } = await popup(["api", "webapp"], {
    summary: SEARCH_SUMMARY,
    repos: { repos: ["api", "webapp"], selected: "api", show: true, newest: {} },
  });
  const field = wrapper.find("#target");

  await field.setValue("7");
  await field.trigger("keydown", { key: "Enter" });
  await flushPromises();
  assert.equal(wrapper.find("[aria-selected=true] .repo-option-name").text(), "webapp");
  wrapper.unmount();
});

test("with one listed repo a bare number opens on the first Enter", async () => {
  vi.spyOn(window, "close").mockImplementation(noop);
  const { wrapper, sent } = await popup(["webapp"], { goTo: { opened: "acme/webapp#76" } });
  const field = wrapper.find("#target");

  await field.setValue("76");
  await field.trigger("keydown", { key: "Enter" });
  await flushPromises();
  assert.deepEqual(withoutPolls(sent), [{ type: "goTo", input: "76" }]);
  wrapper.unmount();
});

test("closing the only filtered match refreshes counts and focuses search without closing the popup", async () => {
  const closeWindow = vi.spyOn(window, "close").mockImplementation(noop);
  const { wrapper, answers, sent } = await searchablePopup();
  try {
    await wrapper.find("#target").setValue("Amber");
    await flushPromises();
    answers.summary = { prs: 1, groups: SEARCH_SUMMARY.groups.slice(1) };
    await wrapper.find("button[data-pr-close]").trigger("click");
    await flushPromises();
    assert.equal(wrapper.findAll("button[aria-expanded]:not(#actions-menu)").length, 0);
    assert.ok(wrapper.find("footer").text().startsWith("0 of 1 PRs"));
    assert.equal(document.activeElement?.id, "target");
    assert.equal(closeWindow.mock.calls.length, 0);
    assert.deepEqual(
      sent.filter((message) => message.type === "closePr" || message.type === "focusPr"),
      [{ type: "closePr", key: "acme/webapp#7", tabId: 7 }],
    );
    assert.equal(wrapper.find("#status").exists(), false);
  } finally {
    wrapper.unmount();
  }
});
