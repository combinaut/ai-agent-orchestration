# Chrome PR tabs

A Chrome extension for PR tabs in `ORCH_PR_TABS_ORG`. It sorts PR tabs into named groups, jumps to a PR, opens your open PRs, and closes tabs whose PR is merged or closed.

## ✨ What it does

- **Groups** every unpinned PR tab by the architect owning its checked-out head branch, or by repository name when no owner is available. Group colours are a stable hash of the name. Ownership changes are picked up on the existing check cadence. New tabs sort themselves when they load.
- **Go to a PR**: paste a PR number or URL and press Enter. A number uses `ORCH_PR_TABS_ORG` and the repo picked in the dropdown below the field (`ORCH_PR_TABS_DEFAULT_REPO` until you pick; the dropdown shows while the input is a bare number and `ORCH_PR_TABS_REPOS` plus the default make two or more, and remembers your last pick); `repo#123` goes to a listed repo; a URL decides the repo, but only when its owner is `ORCH_PR_TABS_ORG`; a PR URL of another owner is rejected. An open tab for that PR is focused (its window comes forward, its group expands); otherwise the PR opens in its group.
- **Open all my open PRs** opens up to 30 PRs matching `ORCH_PR_TABS_OPEN_ALL`, in the order defined by that query, skipping ones already open. Over 30, the badge shows how many are left (`+N`), or an estimate (`~N`, "about N more") when not every search result was fetched. Click again for the next batch.
- **Close merged PRs** runs the merged/closed check at once. The popup says what it closed, which merged tab stayed because it is in view, or the error.
- **Close duplicate tabs** keeps one tab per PR and closes the rest, across all normal windows (incognito is skipped). It keeps the tab in view in the focused window if it is one of them, else a tab in view in another window, else the oldest (lowest tab ID). The popup says how many it closed. Two tabs are the same PR when owner, repo (any case) and number match, whatever the sub-page (`/files`, `/commits`), query or `#fragment`. Go to a PR and Open all my open PRs use the same match, so they focus or skip an open tab instead of adding a double.
- **Automatic close** removes every background tab whose PR is merged or closed. The tab in view stays until you leave it. The check runs 30 seconds after the extension loads or Chrome starts, then every 4 minutes while PR tabs are open. It does not run when none are open. After a failed check it waits longer, with jitter, up to 30 minutes. It returns to 4 minutes once a check succeeds.
- The popup, from the toolbar button, shows how many PRs are open in how many groups, with each group's colour and count. Light and dark follow the system.

GitHub access goes through your local `gh` login, via a native messaging host. No token is stored in the browser.

## 📦 Install

1. Run the [install command](../README.md#-1-install). With a Chrome profile present it renders `~/.claude/chrome-pr-tabs/pr-tabs-host` (a launcher using `ORCH_PYTHON3` and `ORCH_PATH`, which must reach `gh` and `git`, plus `herdr` unless `ORCH_HERDR_BIN` supplies its executable) and registers it in Chrome's `NativeMessagingHosts` directory (macOS: `~/Library/Application Support/Google/Chrome/NativeMessagingHosts/`, Linux: `~/.config/google-chrome/NativeMessagingHosts/`). Without a profile it prints `skipped`.
2. Load the extension once. Chrome cannot load an unpacked extension from a script, so this step is manual:
   1. Open `chrome://extensions` and turn on **Developer mode**.
   2. Click **Load unpacked** and choose `chrome/pr-tabs/extension` inside the clone (`~/.claude/ai-agent-orchestration/chrome/pr-tabs/extension` unless you set `install.repo_path` in `~/.claude/orchestration.toml`).
   3. Pin **PR tabs** from the puzzle-piece menu so the button stays in the toolbar.

   While no Chrome profile has the extension (read from each profile's `Preferences` and `Secure Preferences`, never written), the installer helps: it copies the extension path to the clipboard (`pbcopy`; on Linux `wl-copy` or `xclip`), opens `chrome://extensions` in Chrome, and prints one `load once` line:
   - macOS: `Developer mode on → Load unpacked → ⌘⇧G → paste → Open`
   - Linux: `Developer mode on → Load unpacked → Ctrl+L → paste → Open`

   Without a clipboard tool the line shows the path instead of `paste`. Once loaded, and over SSH or without a display, it stays quiet. The manifest pins a `key`, so the ID is `dkchnpimpkalielhfanmgikajmfconfa` on every machine and the registered host accepts it.

After an update, run the install command, then click the reload icon on the extension card at `chrome://extensions`.

## 🔩 Settings and ownership

`pr_tabs.org` selects the organization, `pr_tabs.default_repo` is the repo for bare numbers, `pr_tabs.repos` adds more repos to pick from (environment form: comma-separated `ORCH_PR_TABS_REPOS`), and `pr_tabs.open_all` is the GitHub search behind the button. Their environment overrides are `ORCH_PR_TABS_ORG`, `ORCH_PR_TABS_DEFAULT_REPO`, `ORCH_PR_TABS_REPOS` and `ORCH_PR_TABS_OPEN_ALL`. Reload the extension after changing settings.

The native host asks the selected env adapter, through shared open-work discovery, for checked-out branches and their workspace owners. It caches the results for 30 seconds. Herdr resolves the worker workspace's `owner` token to the architect workspace label.

- Repository identity and branch must both match.
- Missing owners, unavailable adapters and conflicting ownership fall back to the repo name.
- A failed scan keeps the last good owner map until a check succeeds. A successful scan with no owners restores repo grouping.
- Pinned tabs stay untouched.
- The installer removes retired topic rules from existing personal config and prints one migration line.

## 🛠 Develop

The extension is Vue 3 with PrimeVue 4 (Aura theme, dark mode from the system) and TypeScript. Sources live in `chrome/pr-tabs/src`: `background.ts` (service worker), `core.ts` (pure logic), `messages.ts` (typed popup ↔ worker messages), `types.ts` (every shared type), `popup/` (`App.vue`, `components/`, `tokens.css` for the `--pr-tabs-*` design tokens) and `composables/` (popup logic on VueUse). Static files in `chrome/pr-tabs/public` (`manifest.json`, `rules.json`). Vite builds them into `chrome/pr-tabs/extension`, the folder Chrome loads. The build is committed, so installing needs no Node.

Needs Node 22.12+, 24 or 26+ (the range Vite and Vitest support) and pnpm (the version `packageManager` in `package.json` pins). In `chrome/pr-tabs`:

| Command | Does |
|---|---|
| `pnpm install` | Installs the pinned dependencies from `pnpm-lock.yaml`. |
| `pnpm build` | Builds `extension/` with fixed file names. Commit the result. |
| `pnpm test` | Runs the Vitest suite with v8 coverage; under 80 % lines or branches fails. Component specs use happy-dom, the host tests need `python3`. For one file: `pnpm exec vitest run --coverage.enabled=false test/<file>`. |
| `pnpm lint` | oxlint (braces, no nested ternaries, no `any` or `!`, short functions) and Prettier (`pnpm format` rewrites). `test/templates.test.ts` keeps templates to plain variables and `handle*` handlers; `test/tokens.test.ts` bans literal colours and lengths. |
| `pnpm typecheck` | `vue-tsc --noEmit` in strict mode. |
| `pnpm check` | All of the above in that order, build last. |

After `pnpm build`, click the reload icon on the extension card at `chrome://extensions`. CI runs `scripts/ci/pr-tabs.sh` on Ubuntu: lint, types, tests, then a rebuild that fails when `extension/` differs from the commit.

## 🩺 Troubleshooting

These are the common red errors in the popup. **Close merged PRs** puts `GitHub check failed: ` in front of a host error; **Open all my open PRs** shows it bare.

| Popup message | Meaning and fix |
|---|---|
| `Specified native messaging host not found.` or `Access to the specified native messaging host is forbidden.` | The host is not registered, or its `allowed_origins` ID differs from the extension's. Run the install command, then reload the extension. The ID must be `dkchnpimpkalielhfanmgikajmfconfa`; load the extension from the path above so the pinned `key` applies. |
| `gh not found on PATH` | The launcher's `PATH` is `ORCH_PATH`. Add the folder of `gh` to `watchers.path` in `~/.claude/orchestration.toml`, then run the install command. |
| `gh timed out after 30s` | GitHub did not answer in time. Try again. |
| Any other GitHub message, or `gh exited N` | The text is what `gh` or GitHub returned. Follow it. It can be a network failure, a rate limit or a query error; a logged-out `gh` (`gh auth login`) or an account without access to the org's repos is one cause. |
| `native host timed out` or `empty reply from native host` | The host did not answer. Read the host log. |
| `No answer from the PR tabs service worker…` | Reload the extension at `chrome://extensions`. |

The host logs one line per request, with its error if any, to `~/.claude/chrome-pr-tabs/host.log`. At 256 KiB it moves to `host.log.1`. For the extension's own errors, open the **service worker** link on the extension card at `chrome://extensions`.
