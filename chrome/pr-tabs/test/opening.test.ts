import assert from "node:assert/strict";
import { beforeEach, test } from "vitest";
import { fakeChrome, installFakeChrome, world } from "#test/fakeChrome";

installFakeChrome();

const { goTo, openAll } = await import("#/background");
const URL = "https://github.com/acme/webapp/pull/7";

beforeEach(() => {
  world.tabs = [];
  world.store = {};
  world.created = [];
  world.nativeHandler = async () => ({
    ok: true,
    total: 1,
    prs: [{ url: URL, title: "t", headRefName: "b" }],
    hasNextPage: false,
  });
});

const FORMS = [
  "http://github.com/Acme/Webapp/pull/7",
  `${URL}/files?w=1#diff`,
  `${URL}/commits`,
  `${URL}/checks`,
  `${URL}/`,
  `${URL}?query=1`,
  `${URL}#anchor`,
];

for (const url of FORMS) {
  test(`opening reuses ${url} in another window and group`, async () => {
    world.tabs = [{ id: 1, url, windowId: 2, groupId: 8 }];

    assert.deepEqual(await openAll(), { opened: 0, remaining: 0, estimated: false });
    assert.deepEqual(await goTo("7"), { focused: "acme/webapp#7" });
    assert.equal(world.created.length, 0);
  });
}

test("two quick goTo clicks reuse a tab whose navigation is still pending", async () => {
  const create = fakeChrome.tabs.create;

  fakeChrome.tabs.create = async (properties) => {
    const tab = await create(properties);
    tab.pendingUrl = tab.url;
    tab.url = "";

    return tab;
  };

  try {
    await Promise.all([goTo("7"), goTo("7")]);

    assert.equal(world.created.length, 1);
  } finally {
    fakeChrome.tabs.create = create;
  }
});

test("Open all joins overlapping requests and permits a later batch", async () => {
  const { promise: searching, resolve: signalSearch } = Promise.withResolvers<void>();
  const { promise: held, resolve: release } = Promise.withResolvers<void>();
  const answer = world.nativeHandler;
  let searches = 0;

  world.nativeHandler = async (message) => {
    searches++;
    signalSearch();
    await held;

    return answer(message);
  };

  const first = openAll();
  await searching;

  const second = openAll();
  release();
  const replies = await Promise.all([first, second]);

  assert.equal(searches, 1);
  assert.deepEqual(replies[0], replies[1]);
  assert.equal(world.created.length, 1);

  await openAll();

  assert.equal(searches, 2);
});

test("goTo during an in-flight Open all creation cannot create a second tab", async () => {
  const create = fakeChrome.tabs.create;
  const { promise: creating, resolve: signalCreate } = Promise.withResolvers<void>();
  const { promise: held, resolve: release } = Promise.withResolvers<void>();

  fakeChrome.tabs.create = async (properties) => {
    signalCreate();
    await held;

    return create(properties);
  };

  try {
    const opening = openAll();
    await creating;

    const going = goTo("7");
    await new Promise((resolve) => setImmediate(resolve));

    release();
    await Promise.all([opening, going]);

    assert.equal(world.created.length, 1);
    assert.equal(world.tabs[0]?.active, true);
  } finally {
    release();
    fakeChrome.tabs.create = create;
  }
});

test("a failed Open all run permits retry", async () => {
  world.nativeHandler = async () => ({ ok: false, error: "offline" });

  await assert.rejects(openAll(), /offline/);

  world.nativeHandler = async () => ({ ok: true, total: 0, prs: [], hasNextPage: false });

  assert.deepEqual(await openAll(), { opened: 0, remaining: 0, estimated: false });
});
