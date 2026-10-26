import assert from "node:assert/strict";
import { beforeEach, test } from "vitest";
import { fakeChrome, installFakeChrome, world } from "#test/fakeChrome";

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
    assert.deepEqual(background.openingProgress(), { active: false, opened: 0, planned: 0 });
  } finally {
    held.resolve();
    fakeChrome.tabs.create = create;
  }
});

test("progress advances only after each successful opening", async () => {
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

  world.nativeHandler = async () => ({ ok: true, total: 0, prs: [] });
  assert.deepEqual(await background.openReviews(), { opened: 0, remaining: 0, estimated: false });
});
