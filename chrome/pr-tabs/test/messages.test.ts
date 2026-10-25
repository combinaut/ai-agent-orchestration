import assert from "node:assert/strict";
import { test } from "vitest";
import { ask, errorText, failed, messageListener, noAnswer } from "#/messages";

// The listener's answer, or "no answer" when sendResponse is not called within a second.
function deliver(listener: ReturnType<typeof messageListener>, message: unknown): Promise<unknown> {
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve("no answer"), 1000);

    listener(message, {}, (answer: unknown) => {
      clearTimeout(timer);
      resolve(answer);
    });
  });
}

function errorOf(answer: unknown): string {
  assert.ok(typeof answer === "object" && answer !== null && "error" in answer);

  return String(answer.error);
}

test("an async handler answers through sendResponse with the channel kept open", async () => {
  const listener = messageListener({ summary: async () => ({ prs: 2, groups: [] }) });
  let kept;
  const answer = await new Promise<unknown>((resolve) => {
    kept = listener({ type: "summary" }, {}, resolve);
  });

  assert.equal(kept, true);
  assert.deepEqual(answer, { prs: 2, groups: [] });
});

test("a handler that throws answers with its error message", async () => {
  const listener = messageListener({
    openAll: async () => {
      throw new Error("gh: not logged in");
    },
  });

  assert.deepEqual(await deliver(listener, { type: "openAll" }), { error: "gh: not logged in" });
});

test("a handler that returns nothing still answers", async () => {
  const listener = messageListener({ goTo: () => undefined });
  assert.deepEqual(await deliver(listener, { type: "goTo" }), {});
});

test("unknown and inherited message types answer with an error instead of nothing", async () => {
  const listener = messageListener({ summary: async () => ({ prs: 0, groups: [] }) });

  for (const type of ["bogus", "constructor", "toString", undefined]) {
    const answer = await deliver(listener, { type });
    assert.match(errorOf(answer), /Unknown message type/);
  }
});

test("the popup retries once when there is no answer", async () => {
  const replies = [undefined, { prs: 1, groups: [] }];
  let calls = 0;
  const answer = await ask(async () => replies[calls++], { type: "summary" });

  assert.equal(calls, 2);
  assert.deepEqual(answer, { prs: 1, groups: [] });
});

test("the popup shows the real error after the retry", async () => {
  let calls = 0;
  const answer = await ask(
    async () => {
      calls++;
      throw new Error("Could not establish connection. Receiving end does not exist.");
    },
    { type: "summary" },
  );

  assert.equal(calls, 2);
  assert.deepEqual(answer, {
    error: "Could not establish connection. Receiving end does not exist.",
  });
});

test("two silent answers name the stale service worker", async () => {
  assert.deepEqual(await ask(async () => undefined, { type: "summary" }), { error: noAnswer() });
});

test("Open all is sent once, so a lost answer cannot open a second batch", async () => {
  let calls = 0;
  const answer = await ask(
    async () => {
      calls++;

      return undefined;
    },
    { type: "openAll" },
  );

  assert.equal(calls, 1);
  assert.deepEqual(answer, { error: noAnswer() });
});

test("Check now is sent once, so a lost answer cannot poll and close tabs twice", async () => {
  let calls = 0;
  const answer = await ask(
    async () => {
      calls++;

      return undefined;
    },
    { type: "checkNow" },
  );

  assert.equal(calls, 1);
  assert.deepEqual(answer, { error: noAnswer() });
});

test("closeDuplicates is not retried when no answer comes back", async () => {
  let calls = 0;
  const answer = await ask(
    async () => {
      calls++;

      return undefined;
    },
    { type: "closeDuplicates" },
  );

  assert.equal(calls, 1);
  assert.deepEqual(answer, { error: noAnswer() });
});

test("a reply failed only when it carries an error text", () => {
  assert.equal(failed({ error: "gh: not logged in" }), true);
  assert.equal(failed({ error: "" }), false);
  assert.equal(failed({ opened: 1 }), false);
});

test("an error reads as its message, anything else as text", () => {
  assert.equal(errorText(new Error("gh: not logged in")), "gh: not logged in");
  assert.equal(errorText({ message: "" }), "[object Object]");
  assert.equal(errorText("timeout"), "timeout");
});
