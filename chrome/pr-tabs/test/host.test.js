import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { chmodSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { after, test } from "node:test";
import { fileURLToPath } from "node:url";

const HOST = fileURLToPath(new URL("../host/pr_tabs_host.py", import.meta.url));
const dir = mkdtempSync(join(tmpdir(), "pr-tabs-host-"));
after(() => rmSync(dir, { recursive: true, force: true }));

writeFileSync(
  join(dir, "gh"),
  '#!/bin/sh\nprintf "%s\\n" "$*" >>"$GH_CALLS"\ncat "$GH_REPLY"\n[ -z "$GH_STDERR" ] || echo "$GH_STDERR" >&2\nexit "${GH_STATUS:-0}"\n',
);
chmodSync(join(dir, "gh"), 0o755);

function frame(body) {
  const header = Buffer.alloc(4);
  header.writeUInt32LE(body.length);
  return Buffer.concat([header, body]);
}

function ask(message, { reply = {}, status = 0, stderr = "", raw } = {}) {
  const calls = join(dir, `calls-${Math.random()}`);
  writeFileSync(join(dir, "reply.json"), JSON.stringify(reply));
  writeFileSync(calls, "");
  const done = spawnSync("python3", ["-I", HOST], {
    input: raw ?? frame(Buffer.from(JSON.stringify(message))),
    env: { ...process.env, PATH: `${dir}:${process.env.PATH}`, GH_CALLS: calls, GH_REPLY: join(dir, "reply.json"), GH_STATUS: String(status), GH_STDERR: stderr, PR_TABS_LOG: join(dir, "host.log") },
    timeout: 20000,
  });
  assert.equal(done.status, 0, done.stderr.toString());
  const size = done.stdout.readUInt32LE(0);
  assert.equal(done.stdout.length, 4 + size);
  return { reply: JSON.parse(done.stdout.subarray(4).toString()), calls: readFileSync(calls, "utf8").split("\n").filter(Boolean) };
}

const prs = Array.from({ length: 3 }, (_, i) => ({ owner: "acme", repo: "webapp", number: i + 1 }));

test("settings rejects an explicitly selected missing or non-file config", () => {
  const saved = process.env.ORCH_CONFIG;
  const savedOrg = process.env.ORCH_PR_TABS_ORG;
  try {
    process.env.ORCH_PR_TABS_ORG = "acme";
    for (const path of [join(dir, "missing.toml"), dir]) {
      process.env.ORCH_CONFIG = path;
      const result = ask({ type: "settings" });
      assert.equal(result.reply.ok, false, `expected error for ${path}`);
      assert.match(result.reply.error, /Cannot load PR-tabs settings/);
      assert.equal(result.calls.length, 0);
    }
  } finally {
    if (saved === undefined) delete process.env.ORCH_CONFIG;
    else process.env.ORCH_CONFIG = saved;
    if (savedOrg === undefined) delete process.env.ORCH_PR_TABS_ORG;
    else process.env.ORCH_PR_TABS_ORG = savedOrg;
  }
});

test("states for many PRs take one gh call and come back keyed by PR", () => {
  const config = join(dir, "orchestration.toml");
  const tabs = 'org = "acme"\ndefault_repo = "webapp"\n';
  writeFileSync(config, `[pr_tabs]\n${tabs}`);
  const saved = process.env.ORCH_CONFIG;
  process.env.ORCH_CONFIG = config;
  assert.deepEqual(ask({ type: "settings" }).reply, {
    ok: true,
    org: "acme",
    defaultRepo: "webapp",
    openAll: "",
    topics: [],
  });
  writeFileSync(
    config,
    `[pr_tabs]\n${tabs}topics = [ { name = "Webapp", color = "blue", repo = ["^webapp$"] } ]\n`,
  );
  assert.deepEqual(ask({ type: "settings" }).reply.topics, [
    { name: "Webapp", color: "blue", repo: ["^webapp$"] },
  ]);
  writeFileSync(
    config,
    `[pr_tabs]\n${tabs}topics = [ { name = "Webapp", color = "chartreuse", repo = ["^webapp$"] } ]\n`,
  );
  const invalidColor = ask({ type: "settings" });
  assert.equal(invalidColor.reply.ok, false);
  assert.match(invalidColor.reply.error, /ORCH_PR_TABS_TOPICS/);
  process.env.ORCH_PR_TABS_TOPICS = "";
  assert.deepEqual(ask({ type: "settings" }).reply.topics, []);
  delete process.env.ORCH_PR_TABS_TOPICS;
  writeFileSync(config, `[pr_tabs]\n${tabs}topics = {}\n`);
  assert.equal(ask({ type: "settings" }).reply.ok, false);
  writeFileSync(config, `[pr_tabs]\n${tabs}topics = "not json"\n`);
  assert.equal(ask({ type: "settings" }).reply.ok, false);
  writeFileSync(config, `[pr_tabs]\n${tabs}topics = []\n`);
  process.env.ORCH_PR_TABS_DEFAULT_REPO = "";
  assert.equal(ask({ type: "settings" }).reply.defaultRepo, "");
  delete process.env.ORCH_PR_TABS_DEFAULT_REPO;
  writeFileSync(config, '[pr_tabs]\norg = ""\ndefault_repo = ""\n');
  assert.deepEqual(ask({ type: "settings" }).reply, {
    ok: true,
    org: "",
    defaultRepo: "",
    openAll: "",
    topics: [],
  });
  for (const [key, name, value] of [
    ["ORCH_PR_TABS_ORG", "org", "bad org"],
    ["ORCH_PR_TABS_ORG", "org", "a".repeat(40)],
    ["ORCH_PR_TABS_DEFAULT_REPO", "default_repo", "bad/repo"],
    ["ORCH_PR_TABS_DEFAULT_REPO", "default_repo", "a".repeat(101)],
  ]) {
    writeFileSync(config, `[pr_tabs]\n${name} = "${value}"\n`);
    const invalidName = ask({ type: "settings" });
    assert.equal(invalidName.reply.ok, false);
    assert.match(invalidName.reply.error, new RegExp(key));
    assert.equal(invalidName.calls.length, 0);
  }
  writeFileSync(
    config,
    Buffer.concat([
      Buffer.from('[pr_tabs]\norg = "'),
      Buffer.from([255]),
      Buffer.from('"\n'),
    ]),
  );
  const badEncoding = ask({ type: "settings" });
  assert.equal(badEncoding.reply.ok, false);
  assert.match(badEncoding.reply.error, /Cannot load PR-tabs settings/);
  writeFileSync(config, `[guard]\nbig_repo = true\n[pr_tabs]\n${tabs}`);
  const wrongScope = ask({ type: "settings" });
  assert.equal(wrongScope.reply.ok, false);
  assert.match(wrongScope.reply.error, /Cannot load PR-tabs settings/);
  writeFileSync(config, '[pr_tabs]\norg = "unterminated\n');
  const broken = ask({ type: "settings" });
  assert.equal(broken.reply.ok, false);
  assert.match(broken.reply.error, /Cannot load PR-tabs settings/);
  assert.equal(broken.calls.length, 0);
  if (saved === undefined) delete process.env.ORCH_CONFIG;
  else process.env.ORCH_CONFIG = saved;
  const data = {
    p0: { pullRequest: { state: "MERGED", title: "One", headRefName: "a" } },
    p1: { pullRequest: { state: "OPEN", title: "Two", headRefName: "b" } },
    p2: null,
  };
  const { reply, calls } = ask({ type: "states", prs }, { reply: { data } });
  assert.equal(calls.length, 1);
  assert.deepEqual(reply, {
    ok: true,
    prs: {
      "acme/webapp#1": { state: "MERGED", title: "One", headRefName: "a" },
      "acme/webapp#2": { state: "OPEN", title: "Two", headRefName: "b" },
      "acme/webapp#3": null,
    },
  });
});

test("partial GraphQL errors still return the PRs that resolved", () => {
  const data = { p0: { pullRequest: { state: "OPEN", title: "One", headRefName: "a" } }, p1: null, p2: null };
  const { reply } = ask({ type: "states", prs }, { reply: { data, errors: [{ message: "Could not resolve" }] }, status: 1 });
  assert.equal(reply.ok, true);
  assert.equal(reply.prs["acme/webapp#1"].state, "OPEN");
});

test("gh failure becomes an error reply", () => {
  const { reply } = ask({ type: "states", prs }, { reply: "", status: 4, stderr: "gh: not logged in" });
  assert.deepEqual(reply, { ok: false, error: "gh: not logged in" });
});

test("more PRs than the cap are refused without calling gh", () => {
  const many = Array.from({ length: 101 }, (_, i) => ({ owner: "acme", repo: "webapp", number: i + 1 }));
  const { reply, calls } = ask({ type: "states", prs: many });
  assert.equal(reply.ok, false);
  assert.equal(calls.length, 0);
});

test("names that are not GitHub names are refused without calling gh", () => {
  const { reply, calls } = ask({ type: "states", prs: [{ owner: "acme", repo: "a b){", number: 1 }] });
  assert.equal(reply.ok, false);
  assert.equal(calls.length, 0);
});

test("an oversized request is refused without reading it", () => {
  const body = Buffer.from(JSON.stringify({ type: "search", query: "x".repeat(70 * 1024), limit: 1 }));
  const { reply, calls } = ask(null, { raw: frame(body) });
  assert.equal(reply.ok, false);
  assert.match(reply.error, /too large/);
  assert.equal(calls.length, 0);
});

test("search returns the hits, the total and the next page in one gh call", () => {
  const nodes = [{ url: "https://github.com/acme/webapp/pull/1", title: "One", headRefName: "a" }, {}];
  const search = { issueCount: 140, pageInfo: { endCursor: "Y3Vyc29yOjEwMA==", hasNextPage: true }, nodes };
  const { reply, calls } = ask({ type: "search", query: "is:pr author:tester", limit: 100 }, { reply: { data: { search } } });
  assert.equal(calls.length, 1);
  assert.doesNotMatch(calls[0], / a=/);
  assert.match(calls[0], /author:tester/);
  assert.deepEqual(reply, { ok: true, total: 140, prs: [nodes[0]], endCursor: "Y3Vyc29yOjEwMA==", hasNextPage: true });
});

test("search continues after the given cursor", () => {
  const search = { issueCount: 140, pageInfo: { endCursor: null, hasNextPage: false }, nodes: [] };
  const { reply, calls } = ask({ type: "search", query: "is:pr", limit: 100, after: "Y3Vyc29yOjEwMA==" }, { reply: { data: { search } } });
  assert.match(calls[0], / a=Y3Vyc29yOjEwMA==/);
  assert.equal(reply.hasNextPage, false);
});

test("an unknown request type is an error reply", () => {
  const { reply } = ask({ type: "shell", command: "rm" });
  assert.equal(reply.ok, false);
  assert.match(reply.error, /unknown request type/);
});

test("every request leaves one log line with the states it returned", () => {
  const data = { p0: { pullRequest: { state: "MERGED", title: "One", headRefName: "a" } }, p1: null, p2: null };
  writeFileSync(join(dir, "host.log"), "");
  ask({ type: "states", prs }, { reply: { data } });
  ask({ type: "shell" });
  const lines = readFileSync(join(dir, "host.log"), "utf8").trim().split("\n").map((line) => JSON.parse(line));
  assert.equal(lines.length, 2);
  assert.deepEqual(lines[0].states, { "acme/webapp#1": "MERGED", "acme/webapp#2": null, "acme/webapp#3": null });
  assert.match(lines[1].error, /unknown request type/);
});

test("a log line that would pass 256 KiB first rotates the log", () => {
  const log = join(dir, "host.log");
  writeFileSync(log, "x".repeat(256 * 1024 - 10) + "\n");
  ask({ type: "shell" });
  assert.ok(readFileSync(log).length < 1024);
  assert.match(readFileSync(log, "utf8"), /unknown request type/);
  assert.equal(readFileSync(`${log}.1`).length, 256 * 1024 - 9);
});

test("a search that did not resolve returns the GraphQL error, not an empty success", () => {
  const { reply } = ask(
    { type: "search", query: "is:pr", limit: 100 },
    { reply: { data: { search: null }, errors: [{ message: "API rate limit exceeded" }] }, status: 1 },
  );
  assert.deepEqual(reply, { ok: false, error: "API rate limit exceeded" });
});

test("states where no PR resolved return the GraphQL errors, not an empty success", () => {
  const data = { p0: null, p1: { pullRequest: null }, p2: null };
  const errors = [{ message: "Could not resolve to a Repository" }, { message: "Something went wrong" }];
  const { reply } = ask({ type: "states", prs }, { reply: { data, errors }, status: 1 });
  assert.deepEqual(reply, { ok: false, error: "Could not resolve to a Repository; Something went wrong" });
});

test("states where no PR resolved and gh exited 0 report no data, not an exit code", () => {
  const data = { p0: null, p1: { pullRequest: null }, p2: null };
  const { reply } = ask({ type: "states", prs }, { reply: { data } });
  assert.deepEqual(reply, { ok: false, error: "GitHub returned no data" });
});

test("a request type that is not a string gets a framed error reply", () => {
  for (const type of [["states"], { a: 1 }, 7, null]) {
    const { reply } = ask({ type });
    assert.equal(reply?.ok, false);
    assert.match(reply.error, /unknown request type/);
  }
});
