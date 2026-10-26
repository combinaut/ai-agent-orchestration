import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { test } from "vitest";

const background = new URL("../extension/background.js", import.meta.url).href;
const PROBE = `
const sent = [];
let listener;
const event = { addListener() {} };
globalThis.chrome = {
  tabs: { onUpdated: event, onRemoved: event },
  alarms: { onAlarm: event },
  runtime: {
    onStartup: event, onInstalled: event,
    onMessage: { addListener(value) { listener = value; } },
    sendNativeMessage(host, message, respond) {
      sent.push(message);
      respond({ ok: true, pane: "architect:p1" });
    },
  },
};
await import(process.argv[1]);
let kept;
const reply = await new Promise(resolve => {
  kept = listener({ type: "focusPane", head: { repo: "acme/webapp", branch: "feature" } }, {}, resolve);
});
console.log(JSON.stringify({ kept, reply, sent }));
`;

test("the committed MV3 router keeps the channel open and forwards focusPane to the host", () => {
  const done = spawnSync(process.execPath, ["--input-type=module", "-e", PROBE, background], {
    encoding: "utf8",
    timeout: 5000,
  });

  assert.equal(done.status, 0, done.stderr);

  const result: unknown = JSON.parse(done.stdout.trim().split("\n").at(-1) ?? "null");

  assert.deepEqual(result, {
    kept: true,
    reply: { pane: "architect:p1" },
    sent: [{ type: "focusPane", repo: "acme/webapp", branch: "feature" }],
  });
});
