import assert from "node:assert/strict";
import { beforeEach, test, vi } from "vitest";
import { fakeChrome, installFakeChrome, world } from "#test/fakeChrome";

import type { HostOk, SearchReply } from "#/types";

installFakeChrome();

const background = await import("#/background");
const URL = "https://github.com/acme/webapp/pull/7";

beforeEach(() => {
  world.tabs = [];
  world.store = {};
  world.created = [];
  world.nativeHandler = async () => ({
    ok: true,
    total: 1,
    prs: [{ url: URL, title: "Review me", headRefName: "review" }],
  });
});

test("reviews search open requested PRs in the configured org", async () => {
  const answer = world.nativeHandler;
  let query: string | undefined;
  world.nativeHandler = async (message) => {
    query = "query" in message ? message.query : undefined;

    return answer(message);
  };

  assert.deepEqual(await background.openReviews(), { opened: 1, remaining: 0, estimated: false });
  assert.equal(query, "is:pr is:open org:acme review-requested:@me");
});

test("both batch actions share one run while searching", async () => {
  const held = Promise.withResolvers<void>();
  const searching = Promise.withResolvers<void>();
  const answer = world.nativeHandler;
  let searches = 0;
  world.nativeHandler = async (message) => {
    searches++;
    searching.resolve();
    await held.promise;

    return answer(message);
  };

  const first = background.openAll();
  await searching.promise;
  const second = background.openReviews();
  held.resolve();
  await Promise.all([first, second]);

  assert.equal(searches, 1);
  assert.equal(world.created.length, 1);
});

test("reviews reuse canonical pending tabs and serialize with goTo", async () => {
  world.tabs = [{ id: 1, url: "", pendingUrl: "http://github.com/Acme/Webapp/pull/7/files#x" }];

  await background.openReviews();
  assert.equal(world.created.length, 0);

  world.tabs = [];
  await Promise.all([background.openReviews(), background.goTo("7")]);
  assert.equal(world.created.length, 1);
});

test("progress starts during search, counts actual openings and clears on completion", async () => {
  const held = Promise.withResolvers<void>();
  const creating = Promise.withResolvers<void>();
  const create = fakeChrome.tabs.create;
  fakeChrome.tabs.create = async (properties) => {
    creating.resolve();
    await held.promise;

    return create(properties);
  };

  try {
    const running = background.openReviews();
    assert.deepEqual(background.openingProgress(), { active: true, opened: 0, planned: 0 });
    await creating.promise;
    assert.deepEqual(background.openingProgress(), { active: true, opened: 0, planned: 1 });
    held.resolve();
    await running;
    assert.deepEqual(background.openingProgress(), {
      active: false,
      opened: 0,
      planned: 0,
      result: { opened: 1, remaining: 0, estimated: false },
    });
  } finally {
    held.resolve();
    fakeChrome.tabs.create = create;
  }
});

test("progress advances as planned PRs become ready", async () => {
  const held = Promise.withResolvers<void>();
  const creatingSecond = Promise.withResolvers<void>();
  const create = fakeChrome.tabs.create;
  let calls = 0;
  world.nativeHandler = async () => ({
    ok: true,
    total: 2,
    prs: [{ url: URL }, { url: URL.replace("/7", "/8") }],
  });
  fakeChrome.tabs.create = async (properties) => {
    if (++calls === 2) {
      creatingSecond.resolve();
      await held.promise;
    }

    return create(properties);
  };

  try {
    const running = background.openReviews();
    await creatingSecond.promise;
    assert.deepEqual(background.openingProgress(), { active: true, opened: 1, planned: 2 });
    held.resolve();
    await running;
  } finally {
    held.resolve();
    fakeChrome.tabs.create = create;
  }
});

test("failed review searches clear progress and allow a new batch", async () => {
  world.nativeHandler = async () => ({ ok: false, error: "offline" });

  await assert.rejects(background.openReviews(), /offline/);
  assert.equal(background.openingProgress().active, false);
  assert.deepEqual(background.openingProgress().result, { error: "offline" });

  world.nativeHandler = async () => ({ ok: true, total: 0, prs: [] });
  assert.deepEqual(await background.openReviews(), { opened: 0, remaining: 0, estimated: false });
});

test("progress waits for the first PR metadata write", async () => {
  const held = Promise.withResolvers<void>();
  const writing = Promise.withResolvers<void>();
  const set = fakeChrome.storage.local.set;
  fakeChrome.storage.local.set = async (values) => {
    if (values.meta?.["acme/webapp#7"]) {
      writing.resolve();
      await held.promise;
    }
    await set(values);
  };
  const batch = background.openReviews();

  try {
    await writing.promise;
    assert.deepEqual(background.openingProgress(), { active: true, opened: 0, planned: 1 });
    held.resolve();
    await batch;
    assert.equal(world.store.meta?.["acme/webapp#7"]?.headRefName, "review");
  } finally {
    held.resolve();
    await batch;
    fakeChrome.storage.local.set = set;
  }
});

function searchReply(count: number, start = 7): HostOk<SearchReply> {
  const prs = Array.from({ length: count }, (_value, index) => ({
    url: `https://github.com/acme/webapp/pull/${start + index}`,
    title: "Review",
    headRefName: "branch",
  }));

  return { ok: true, total: count, prs };
}

for (const order of ["own-first", "reviews-first"] as const) {
  test(`only Open all owns the badge: ${order}`, async () => {
    const set = fakeChrome.action.setBadgeText;
    let badge = "+9";
    fakeChrome.action.setBadgeText = async (details) => {
      badge = details.text ?? "";
    };

    try {
      if (order === "own-first") {
        world.nativeHandler = async () => searchReply(42);
        await background.openAll();
        assert.equal(badge, "+12");
        world.nativeHandler = async () => searchReply(3, 100);
        await background.openReviews();
        assert.equal(badge, "+12");
      } else {
        world.nativeHandler = async () => searchReply(35);
        await background.openReviews();
        assert.equal(badge, "+9");
        world.nativeHandler = async () => searchReply(0);
        await background.openAll();
        assert.equal(badge, "");
      }
    } finally {
      fakeChrome.action.setBadgeText = set;
    }
  });
}

test("a planned PR opened elsewhere counts toward completed progress", async () => {
  const held = Promise.withResolvers<void>();
  const badging = Promise.withResolvers<void>();
  const create = fakeChrome.tabs.create;
  const badge = fakeChrome.action.setBadgeText;
  world.nativeHandler = async () => searchReply(2);
  fakeChrome.tabs.create = async (properties) => {
    const tab = await create(properties);
    world.tabs.push({ id: 900, url: URL.replace("/7", "/8") });

    return tab;
  };
  fakeChrome.action.setBadgeText = async () => {
    badging.resolve();
    await held.promise;
  };
  const batch = background.openAll();

  try {
    await badging.promise;
    assert.deepEqual(background.openingProgress(), { active: true, opened: 2, planned: 2 });
    held.resolve();
    assert.deepEqual(await batch, { opened: 1, remaining: 0, estimated: false });
  } finally {
    held.resolve();
    await batch;
    fakeChrome.tabs.create = create;
    fakeChrome.action.setBadgeText = badge;
  }
});

test("a failed metadata write warns and the remaining PRs still open", async () => {
  const set = fakeChrome.storage.local.set;
  const warning = vi.spyOn(console, "warn").mockImplementation(() => {});
  const error = new Error("metadata quota");
  let failed = false;
  world.nativeHandler = async () => searchReply(2);
  fakeChrome.storage.local.set = async (values) => {
    if (values.meta?.["acme/webapp#7"] && !failed) {
      failed = true;
      throw error;
    }
    await set(values);
  };

  try {
    assert.deepEqual(await background.openReviews(), { opened: 2, remaining: 0, estimated: false });
    assert.equal(world.created.length, 2);
    assert.ok(warning.mock.calls.some((call) => call.includes(error)));
  } finally {
    fakeChrome.storage.local.set = set;
    warning.mockRestore();
  }
});
