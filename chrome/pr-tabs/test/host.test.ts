import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { chmodSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterAll, afterEach, test, vi } from "vitest";
import { fileURLToPath } from "node:url";

const HOST = fileURLToPath(new URL("../host/pr_tabs_host.py", import.meta.url));
const dir = mkdtempSync(join(tmpdir(), "pr-tabs-host-"));
const config = join(dir, "orchestration.toml");

afterAll(() => rmSync(dir, { recursive: true, force: true }));
afterEach(() => {
  vi.unstubAllEnvs();
});

const FAKE_GH = [
  "#!/bin/sh",
  'printf "%s\\n" "$*" >>"$GH_CALLS"',
  'cat "$GH_REPLY"',
  '[ -z "$GH_STDERR" ] || echo "$GH_STDERR" >&2',
  'exit "${GH_STATUS:-0}"',
  "",
].join("\n");

writeFileSync(join(dir, "gh"), FAKE_GH);
chmodSync(join(dir, "gh"), 0o755);

interface HostAnswer {
  ok?: boolean;
  error?: string;
  [field: string]: unknown;
}

interface Fake {
  reply?: unknown;
  status?: number;
  stderr?: string;
  raw?: Buffer;
}

function frame(body: Buffer) {
  const header = Buffer.alloc(4);
  header.writeUInt32LE(body.length);
  return Buffer.concat([header, body]);
}

function hostEnv(calls: string, fake: Fake): NodeJS.ProcessEnv {
  const { status = 0, stderr = "" } = fake;

  return {
    ...process.env,
    HOME: dir,
    PATH: `${dir}:${process.env.PATH}`,
    GH_CALLS: calls,
    GH_REPLY: join(dir, "reply.json"),
    GH_STATUS: String(status),
    GH_STDERR: stderr,
    ORCH_ENV: "none",
    PR_TABS_LOG: join(dir, "host.log"),
  };
}

function field(value: unknown, key: string): unknown {
  if (typeof value !== "object" || value === null) {
    return undefined;
  }

  return Object.getOwnPropertyDescriptor(value, key)?.value;
}

function errorOf(reply: HostAnswer): string {
  return String(reply.error);
}

function ask(message: unknown, fake: Fake = {}) {
  const calls = join(dir, `calls-${Math.random()}`);

  writeFileSync(join(dir, "reply.json"), JSON.stringify(fake.reply ?? {}));
  writeFileSync(calls, "");

  const done = spawnSync("python3", ["-I", HOST], {
    input: fake.raw ?? frame(Buffer.from(JSON.stringify(message))),
    env: hostEnv(calls, fake),
    timeout: 20000,
  });

  assert.equal(done.status, 0, done.stderr.toString());

  const size = done.stdout.readUInt32LE(0);

  assert.equal(done.stdout.length, 4 + size);

  const reply: HostAnswer = JSON.parse(done.stdout.subarray(4).toString());

  return { reply, calls: readFileSync(calls, "utf8").split("\n").filter(Boolean) };
}

function settingsFor(body: string | Buffer) {
  writeFileSync(config, body);
  vi.stubEnv("ORCH_CONFIG", config);
  return ask({ type: "settings" });
}

function assertRefused(result: ReturnType<typeof ask>, pattern: RegExp, label?: string) {
  assert.equal(result.reply.ok, false, label ?? String(pattern));
  assert.match(errorOf(result.reply), pattern);
  assert.equal(result.calls.length, 0);
}

const prs = Array.from({ length: 3 }, (_, i) => ({ owner: "acme", repo: "webapp", number: i + 1 }));

test("settings rejects an explicitly selected missing or non-file config", () => {
  vi.stubEnv("ORCH_PR_TABS_ORG", "acme");

  for (const path of [join(dir, "missing.toml"), dir]) {
    vi.stubEnv("ORCH_CONFIG", path);
    assertRefused(ask({ type: "settings" }), /Cannot load PR-tabs settings/, path);
  }
});

test("pr_tabs.repos joins default_repo, which stays first, without repeats", () => {
  const repos =
    '[pr_tabs]\norg = "acme"\ndefault_repo = "webapp"\nrepos = ["api", "Webapp", "docs"]\n';
  assert.deepEqual(settingsFor(repos).reply.repos, ["webapp", "api", "docs"]);

  vi.stubEnv("ORCH_PR_TABS_REPOS", " tools , api,,");

  assert.deepEqual(ask({ type: "settings" }).reply.repos, ["webapp", "tools", "api"]);

  vi.unstubAllEnvs();

  const noDefault = settingsFor('[pr_tabs]\norg = "acme"\nrepos = ["api"]\n');

  assert.deepEqual(noDefault.reply.repos, ["api"]);
});

test("pr_tabs.repos refuses an invalid repo name, in the file or the environment", () => {
  for (const bad of ["bad/repo", "a".repeat(101), "a b"]) {
    const refused = settingsFor(`[pr_tabs]\norg = "acme"\nrepos = ["api", "${bad}"]\n`);
    assertRefused(refused, /ORCH_PR_TABS_REPOS/, bad);
  }

  vi.stubEnv("ORCH_PR_TABS_REPOS", "api,bad/repo");

  const envBad = settingsFor('[pr_tabs]\norg = "acme"\n');

  assert.equal(envBad.reply.ok, false);
  assert.match(errorOf(envBad.reply), /ORCH_PR_TABS_REPOS/);
});

const TABS = 'org = "acme"\ndefault_repo = "webapp"\n';

test("settings read org, default repo and repos, and the environment may empty the default", () => {
  assert.deepEqual(settingsFor(`[pr_tabs]\n${TABS}`).reply, {
    ok: true,
    org: "acme",
    defaultRepo: "webapp",
    repos: ["webapp"],
    openAll: "",
  });

  vi.stubEnv("ORCH_PR_TABS_DEFAULT_REPO", "");

  assert.equal(ask({ type: "settings" }).reply.defaultRepo, "");

  vi.unstubAllEnvs();

  assert.deepEqual(settingsFor('[pr_tabs]\norg = ""\ndefault_repo = ""\n').reply, {
    ok: true,
    org: "",
    defaultRepo: "",
    repos: [],
    openAll: "",
  });
});

test("settings refuse an invalid org or default repo", () => {
  for (const [key, name, value] of [
    ["ORCH_PR_TABS_ORG", "org", "bad org"],
    ["ORCH_PR_TABS_ORG", "org", "a".repeat(40)],
    ["ORCH_PR_TABS_DEFAULT_REPO", "default_repo", "bad/repo"],
    ["ORCH_PR_TABS_DEFAULT_REPO", "default_repo", "a".repeat(101)],
  ] as const) {
    assertRefused(settingsFor(`[pr_tabs]\n${name} = "${value}"\n`), new RegExp(key));
  }
});

test("settings refuse a config that is not UTF-8, scoped wrong or broken", () => {
  const latin = Buffer.concat([
    Buffer.from('[pr_tabs]\norg = "'),
    Buffer.from([255]),
    Buffer.from('"\n'),
  ]);

  const cannot = /Cannot load PR-tabs settings/;
  const badEncoding = settingsFor(latin);

  assert.equal(badEncoding.reply.ok, false);
  assert.match(errorOf(badEncoding.reply), cannot);

  const wrongScope = settingsFor(`[guard]\nbig_repo = true\n[pr_tabs]\n${TABS}`);

  assert.equal(wrongScope.reply.ok, false);
  assert.match(errorOf(wrongScope.reply), cannot);
  assertRefused(settingsFor('[pr_tabs]\norg = "unterminated\n'), cannot);
});

test("states for many PRs take one gh call and come back keyed by PR", () => {
  const data = {
    p0: { pullRequest: { state: "MERGED", title: "One", headRefName: "a" } },
    p1: { pullRequest: { state: "OPEN", title: "Two", headRefName: "b" } },
    p2: null,
  };

  const { reply, calls } = ask({ type: "states", prs }, { reply: { data } });

  assert.equal(calls.length, 1);
  assert.deepEqual(reply, {
    ok: true,
    owners: {},
    prs: {
      "acme/webapp#1": { state: "MERGED", title: "One", headRefName: "a" },
      "acme/webapp#2": { state: "OPEN", title: "Two", headRefName: "b" },
      "acme/webapp#3": null,
    },
  });
});

test("partial GraphQL errors still return the PRs that resolved", () => {
  const data = {
    p0: { pullRequest: { state: "OPEN", title: "One", headRefName: "a" } },
    p1: null,
    p2: null,
  };

  const { reply } = ask(
    { type: "states", prs },
    { reply: { data, errors: [{ message: "Could not resolve" }] }, status: 1 },
  );

  assert.equal(reply.ok, true);
  assert.equal(field(field(reply.prs, "acme/webapp#1"), "state"), "OPEN");
});

test("gh failure becomes an error reply", () => {
  const { reply } = ask(
    { type: "states", prs },
    { reply: "", status: 4, stderr: "gh: not logged in" },
  );

  assert.deepEqual(reply, { ok: false, error: "gh: not logged in" });
});

test("more PRs than the cap are refused without calling gh", () => {
  const many = Array.from({ length: 101 }, (_, i) => ({
    owner: "acme",
    repo: "webapp",
    number: i + 1,
  }));

  const { reply, calls } = ask({ type: "states", prs: many });

  assert.equal(reply.ok, false);
  assert.equal(calls.length, 0);
});

test("names that are not GitHub names are refused without calling gh", () => {
  const { reply, calls } = ask({
    type: "states",
    prs: [{ owner: "acme", repo: "a b){", number: 1 }],
  });

  assert.equal(reply.ok, false);
  assert.equal(calls.length, 0);
});

test("an oversized request is refused without reading it", () => {
  const body = Buffer.from(
    JSON.stringify({ type: "search", query: "x".repeat(70 * 1024), limit: 1 }),
  );

  const { reply, calls } = ask(null, { raw: frame(body) });

  assert.equal(reply.ok, false);
  assert.match(errorOf(reply), /too large/);
  assert.equal(calls.length, 0);
});

test("search returns the hits, the total and the next page in one gh call", () => {
  const nodes = [
    { url: "https://github.com/acme/webapp/pull/1", title: "One", headRefName: "a" },
    {},
  ];

  const search = {
    issueCount: 140,
    pageInfo: { endCursor: "Y3Vyc29yOjEwMA==", hasNextPage: true },
    nodes,
  };

  const { reply, calls } = ask(
    { type: "search", query: "is:pr author:tester", limit: 100 },
    { reply: { data: { search } } },
  );

  assert.equal(calls.length, 1);

  const [call = ""] = calls;

  assert.doesNotMatch(call, / a=/);
  assert.match(call, /author:tester/);
  assert.deepEqual(reply, {
    ok: true,
    owners: {},
    total: 140,
    prs: [nodes[0]],
    endCursor: "Y3Vyc29yOjEwMA==",
    hasNextPage: true,
  });
});

test("search continues after the given cursor", () => {
  const search = { issueCount: 140, pageInfo: { endCursor: null, hasNextPage: false }, nodes: [] };
  const { reply, calls } = ask(
    { type: "search", query: "is:pr", limit: 100, after: "Y3Vyc29yOjEwMA==" },
    { reply: { data: { search } } },
  );

  const [call = ""] = calls;

  assert.match(call, / a=Y3Vyc29yOjEwMA==/);
  assert.equal(reply.hasNextPage, false);
});

test("an unknown request type is an error reply", () => {
  const { reply } = ask({ type: "shell", command: "rm" });
  assert.equal(reply.ok, false);
  assert.match(errorOf(reply), /unknown request type/);
});

test("every request leaves one log line with the states it returned", () => {
  const data = {
    p0: { pullRequest: { state: "MERGED", title: "One", headRefName: "a" } },
    p1: null,
    p2: null,
  };

  writeFileSync(join(dir, "host.log"), "");
  ask({ type: "states", prs }, { reply: { data } });
  ask({ type: "shell" });

  const lines = readFileSync(join(dir, "host.log"), "utf8")
    .trim()
    .split("\n")
    .map((line) => JSON.parse(line));
  assert.equal(lines.length, 2);
  assert.deepEqual(lines[0].states, {
    "acme/webapp#1": "MERGED",
    "acme/webapp#2": null,
    "acme/webapp#3": null,
  });
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
    {
      reply: { data: { search: null }, errors: [{ message: "API rate limit exceeded" }] },
      status: 1,
    },
  );

  assert.deepEqual(reply, { ok: false, error: "API rate limit exceeded" });
});

test("states where no PR resolved return the GraphQL errors, not an empty success", () => {
  const data = { p0: null, p1: { pullRequest: null }, p2: null };
  const errors = [
    { message: "Could not resolve to a Repository" },
    { message: "Something went wrong" },
  ];

  const { reply } = ask({ type: "states", prs }, { reply: { data, errors }, status: 1 });

  assert.deepEqual(reply, {
    ok: false,
    error: "Could not resolve to a Repository; Something went wrong",
  });
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
    assert.match(errorOf(reply), /unknown request type/);
  }
});
