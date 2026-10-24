"""Every orchestration setting, its TOML key and scope; the readers of the global and repo files."""

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ImportError:  # Python < 3.11
    tomllib = None

GLOBAL_FILE = "~/.claude/orchestration.toml"
REPO_FILE = ".orchestration.toml"
LOCAL_FILE = ".orchestration.local.toml"
SNIPPET_DIR = "~/.claude/orchestration-repo-snippets"


class Setting:
    def __init__(
        self,
        key,
        env,
        kind,
        default,
        scope,
        doc,
        sep=" ",
        paths=False,
        local_only=False,
        choices=None,
        required=(),
        why="",
        nonempty=False,
        shared=False,
    ):
        self.key, self.env, self.kind, self.default = key, env, kind, default
        self.scope, self.doc, self.sep, self.paths = scope, doc, sep, paths
        self.local_only, self.choices, self.required, self.why = (
            local_only,
            choices,
            required,
            why,
        )
        self.nonempty = nonempty
        self.shared = shared

    @property
    def table(self):
        return self.key.split(".")[0]

    @property
    def name(self):
        return self.key.split(".", 1)[1]


def _g(key, env, kind, default, doc, **kw):
    return Setting(key, env, kind, default, "global", doc, **kw)


def _r(key, env, kind, default, doc, **kw):
    return Setting(key, env, kind, default, "repo", doc, **kw)


class Provider:
    """A worker family and its model settings; need is "all", "one" (that model serves every tier) or "none" (on by default only when a model is set)."""

    def __init__(self, name, models, need="all"):
        self.name, self.models, self.need = name, models, need


PROVIDERS = [
    Provider("claude", ["ORCH_COORDINATOR_MODEL", "ORCH_ARCHITECT_MODEL"]),
    Provider("codex", ["ORCH_CODEX_WORKER_MODEL", "ORCH_CODEX_SMART_MODEL"], "one"),
    Provider("pi", ["ORCH_PI_WORKER_MODEL", "ORCH_PI_SMART_MODEL"], "none"),
]
REQUIRED_PROVIDERS = ["claude"]
CLAUDE_TIERS = ["haiku", "sonnet", "opus", "fable"]

DEFAULT_PATH = [
    "~/.local/bin",
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/usr/bin",
    "/bin",
    "/usr/sbin",
    "/sbin",
]

SETTINGS = [
    _g(
        "pr.author",
        "ORCH_PR_AUTHOR",
        "str",
        "",
        'GitHub login whose open PRs the audit watches in the repos of the open workspaces ("@me" works); required by the audit watcher',
    ),
    _g(
        "pr.since",
        "ORCH_PR_SINCE",
        "str",
        "",
        "Only PRs updated on or after this date (YYYY-MM-DD); empty means no date filter",
    ),
    _g(
        "pr.bot_reviewer_login",
        "ORCH_BOT_REVIEWER_LOGIN",
        "str",
        "copilot-pull-request-reviewer",
        'Bot whose "Needs a closer look" review raises a COPILOT line',
    ),
    _g(
        "audit.packs_max",
        "ORCH_PACKS_MAX",
        "int",
        50,
        "Pack files in a clone of the open work before a PACKS line",
    ),
    _g(
        "audit.worktrees_max",
        "ORCH_WORKTREES_MAX",
        "int",
        40,
        "Worktrees in a clone of the open work before a WORKTREES line",
    ),
    _g(
        "audit.page_prs",
        "ORCH_AUDIT_PAGE_PRS",
        "int",
        80,
        "PR search page size (1-100)",
    ),
    _g(
        "audit.page_nodes",
        "ORCH_AUDIT_PAGE_NODES",
        "int",
        100,
        "Nested GitHub query page size (1-100); a cut-off result prints TRUNCATED",
    ),
    _g(
        "legacy.scans_max",
        "ORCH_LEGACY_SCANS_MAX",
        "int",
        10,
        "PRs scanned for legacy code per audit pass; the rest wait for the next pass",
    ),
    _g(
        "legacy.reads_max",
        "ORCH_LEGACY_READS_MAX",
        "int",
        20,
        "File reads per PR scan for files GitHub returns without a patch",
    ),
    _g(
        "legacy.map_repo",
        "ORCH_LEGACY_MAP_REPO",
        "str",
        "",
        "Clone the daily legacy-map job reads; empty picks, at install time, the clone of the open work whose repo settings turn legacy.scan on",
        paths=True,
    ),
    _g(
        "heavy_slots.max",
        "ORCH_HEAVY_SLOTS_MAX",
        "int",
        4,
        "Most heavy jobs at once on the machine",
    ),
    _g(
        "heavy_slots.dir",
        "ORCH_HEAVY_SLOTS_DIR",
        "str",
        "",
        "Slot directory; empty means ~/.claude/heavy-slots",
        paths=True,
    ),
    _g(
        "heavy_slots.kill_grace",
        "ORCH_HEAVY_SLOTS_KILL_GRACE",
        "int",
        10,
        "Seconds a stopped job gets before it is killed",
    ),
    _g(
        "providers.enabled",
        "ORCH_PROVIDERS",
        "list",
        [],
        "Worker families this machine uses (claude, codex, pi); claude is required, the Coordinator and the architects run on it. Empty means claude and codex, plus pi when a pi model is set. A family left out is never started or asked for, and its model settings may stay empty",
        sep=",",
        choices=[p.name for p in PROVIDERS],
        required=REQUIRED_PROVIDERS,
        why="the Coordinator and the architects run on it",
    ),
    _g(
        "providers.claude",
        "ORCH_CLAUDE_TIERS",
        "list",
        list(CLAUDE_TIERS),
        "Claude worker tiers the architects may pick (haiku, sonnet, opus, fable); at least one. The Coordinator and architect models are their own settings",
        sep=",",
        choices=CLAUDE_TIERS,
        nonempty=True,
    ),
    _g(
        "models.coordinator",
        "ORCH_COORDINATOR_MODEL",
        "str",
        "claude-opus-5-5",
        "Model of the Coordinator",
    ),
    _g(
        "models.architect",
        "ORCH_ARCHITECT_MODEL",
        "str",
        "claude-opus-5-5",
        "Model of the architects",
    ),
    _g(
        "models.codex_worker",
        "ORCH_CODEX_WORKER_MODEL",
        "str",
        "<codex-worker-model>",
        "Codex model for routine work; empty or a placeholder means codex_smart serves it too. With codex on, set at least one of the two",
    ),
    _g(
        "models.codex_smart",
        "ORCH_CODEX_SMART_MODEL",
        "str",
        "<codex-smart-model>",
        "Codex model for hard work; empty or a placeholder means codex_worker serves it too. With codex on, set at least one of the two",
    ),
    _g(
        "models.pi_worker",
        "ORCH_PI_WORKER_MODEL",
        "str",
        "",
        "pi model for routine work as provider/model-id; empty means pi is not used on this machine",
    ),
    _g(
        "models.pi_smart",
        "ORCH_PI_SMART_MODEL",
        "str",
        "",
        "pi model for hard work as provider/model-id; empty means pi is not used on this machine",
    ),
    _g(
        "remote_control.architects",
        "ORCH_REMOTE_CONTROL_ARCHITECTS",
        "bool",
        True,
        "Give architect sessions Remote Control on start and recycle",
    ),
    _g(
        "recycle.tokens",
        "ORCH_RECYCLE_TOKENS",
        "int",
        300000,
        "Tokens used at which RECYCLE fires",
    ),
    _g(
        "recycle.pct",
        "ORCH_RECYCLE_PCT",
        "number",
        55,
        "Context percentage used when the token sensor is absent",
    ),
    _g(
        "recycle.park_idle",
        "ORCH_PARK_IDLE",
        "bool",
        False,
        "Park architects that have no open PR, worker or watcher",
    ),
    _g(
        "install.repo_path",
        "ORCH_REPO_PATH",
        "str",
        "",
        "The clone install.sh keeps up to date and links into ~/.claude; empty means ~/.claude/ai-agent-orchestration",
        paths=True,
    ),
    _g(
        "install.env",
        "ORCH_ENV",
        "str",
        "",
        'Env adapter install.sh runs without --env (a directory under env/); empty means herdr, "none" skips the env step',
    ),
    _g(
        "install.radar_path",
        "ORCH_RADAR_PATH",
        "str",
        "",
        "Herdr Radar checkout; empty means ~/.claude/herdr-radar",
        paths=True,
    ),
    _g(
        "watchers.service",
        "ORCH_WATCHERS_SERVICE",
        "bool",
        False,
        "Register the watchers as launchd or systemd user services; needs herdr.bin and herdr.socket",
    ),
    _g(
        "watchers.python3",
        "ORCH_PYTHON3",
        "str",
        "python3",
        "Interpreter for the watcher scripts (3.11 or newer); services get it, or the installer's Python 3.11+ when it is older",
        paths=True,
    ),
    _g(
        "watchers.pr_scope",
        "ORCH_WATCHERS_PR_SCOPE",
        "str",
        "",
        'GitHub search scope for the PR watchers, for example "org:acme"',
    ),
    _g(
        "watchers.worker_model",
        "ORCH_WATCHERS_WORKER_MODEL",
        "str",
        "",
        "Model label in dispatch records; empty leaves it out",
    ),
    _g(
        "watchers.primary_repo",
        "ORCH_WATCHERS_PRIMARY_REPO",
        "str",
        "",
        "Primary owner/name repo in dispatch records",
    ),
    _g(
        "watchers.path",
        "ORCH_PATH",
        "list",
        DEFAULT_PATH,
        "PATH entries given to services and to the Chrome host",
        sep=":",
        paths=True,
    ),
    _g(
        "watchers.closed_dir",
        "ORCH_CLOSED_DIR",
        "str",
        "",
        "Where close-pane.sh marks a pane as closed on purpose; empty means ~/.claude/coordinator/watchers/prod-context/closed. The workers watcher reads <state-dir>/closed, so a custom folder needs that --state-dir",
        paths=True,
    ),
    _g(
        "herdr.bin",
        "ORCH_HERDR_BIN",
        "str",
        "",
        "Absolute path of the Herdr executable",
        paths=True,
    ),
    _g(
        "herdr.socket",
        "ORCH_HERDR_SOCKET",
        "str",
        "",
        "Absolute path of the Herdr socket",
        paths=True,
    ),
    _g(
        "herdr.todo_popup",
        "ORCH_HERDR_TODO_POPUP",
        "bool",
        True,
        'install.sh links a Herdr plugin that opens the to-do view in a popup titled "Your to-do" on prefix+t, with a marked `[[keys.command]]` block in Herdr\'s `config.toml`; `false` removes both. Needs the herdr env',
    ),
    _g(
        "user_todo.status_dir",
        "ORCH_USER_TODO_STATUS_DIR",
        "str",
        "",
        "To-do status directory; empty means ~/.claude/coordinator/status",
        paths=True,
    ),
    _g(
        "user_todo.output",
        "ORCH_USER_TODO_OUTPUT",
        "str",
        "",
        "To-do output file; empty means ~/.claude/coordinator/user-todo.md",
        paths=True,
    ),
    _g(
        "user_todo.state",
        "ORCH_USER_TODO_STATE",
        "str",
        "",
        "To-do state file; empty means ~/.claude/coordinator/user-todo.state.json",
        paths=True,
    ),
    _g(
        "user_questions.dir",
        "ORCH_USER_QUESTIONS_DIR",
        "str",
        "",
        "Question directory; empty means ~/.claude/coordinator/questions",
        paths=True,
    ),
    _g(
        "host.pressure_min_pct",
        "ORCH_HOST_PRESSURE_MIN_PCT",
        "number",
        20,
        "Host watcher: lowest free memory percentage",
    ),
    _g(
        "host.swap_max_mb",
        "ORCH_HOST_SWAP_MAX_MB",
        "number",
        1024,
        "Host watcher: most swap used, in MB",
    ),
    _g(
        "host.procs_max",
        "ORCH_HOST_PROCS_MAX",
        "number",
        1300,
        "Host watcher: most processes",
    ),
    _g(
        "host.files_max",
        "ORCH_HOST_FILES_MAX",
        "number",
        150000,
        "Host watcher: most open files",
    ),
    _g(
        "host.load_max",
        "ORCH_HOST_LOAD_MAX",
        "number",
        40,
        "Host watcher: highest load average",
    ),
    _g(
        "alerts.escalate_ratio",
        "ORCH_ALERT_ESCALATE_RATIO",
        "number",
        1.5,
        "Feed: ratio between severity steps; greater than 1",
    ),
    _g(
        "alerts.digest_s",
        "ORCH_ALERT_DIGEST_S",
        "int",
        1800,
        "Feed: seconds between digest lines",
    ),
    _g(
        "alerts.quiet_s",
        "ORCH_ALERT_QUIET_S",
        "int",
        1800,
        "Feed: seconds a condition stays clear before its clear line",
    ),
    _g("hog.gb", "ORCH_HOG_GB", "number", 6, "Hog watcher: process size in GB"),
    _g(
        "hog.git_gb",
        "ORCH_HOG_GIT_GB",
        "number",
        2,
        "Hog watcher: git process size in GB",
    ),
    _g(
        "hog.git_age_s",
        "ORCH_HOG_GIT_AGE_S",
        "int",
        180,
        "Hog watcher: git process age in seconds",
    ),
    _g(
        "workers.ask_timeout_s",
        "ORCH_ASK_TIMEOUT_S",
        "int",
        1800,
        "Maximum seconds a worker question waits for an answer",
    ),
    _g(
        "workers.wait_s",
        "ORCH_WAIT_S",
        "number",
        600,
        "Seconds idle or done before WORKER WAITING",
    ),
    _g(
        "workers.wait_repeat_s",
        "ORCH_WAIT_REPEAT_S",
        "number",
        1800,
        "Seconds between waiting alerts (minimum 60)",
    ),
    _g(
        "workers.idle_s",
        "ORCH_WORKER_IDLE_S",
        "int",
        7200,
        "Seconds idle or done before WORKER IDLE (no open PR)",
    ),
    _g(
        "pr_tabs.org",
        "ORCH_PR_TABS_ORG",
        "str",
        "",
        "Organization the PR tabs extension manages; empty turns org actions off",
    ),
    _g(
        "pr_tabs.default_repo",
        "ORCH_PR_TABS_DEFAULT_REPO",
        "str",
        "",
        "Repo name for bare PR numbers; number jumps are off when pr_tabs.repos is empty too",
    ),
    _g(
        "pr_tabs.repos",
        "ORCH_PR_TABS_REPOS",
        "list",
        [],
        "Repo names in pr_tabs.org the popup can send a bare PR number to, besides default_repo; the popup shows a picker when there are two or more",
        sep=",",
    ),
    _g(
        "pr_tabs.open_all",
        "ORCH_PR_TABS_OPEN_ALL",
        "str",
        "",
        "GitHub search behind the Open all button; empty turns it off",
    ),
    _r(
        "guard.big_repo",
        "ORCH_BIG_REPO",
        "bool",
        False,
        "Broad Git reads (git grep, cat-file over all objects) are blocked in this repo",
    ),
    _r(
        "audit.merge_gate_checks",
        "ORCH_MERGE_GATE_CHECKS",
        "list",
        [],
        'Check names that only run on a full CI run; an approved PR on a non-default base reports "full CI ran" or "full CI not run"',
        sep="|",
    ),
    _r(
        "review.finalize_commands",
        "ORCH_REVIEW_FINALIZE_COMMANDS",
        "list",
        ["/finalize-change-footprint", "/finalize-code-comments"],
        "Slash commands workers run before calling a PR done or ready; empty skips this step. The global file sets it for every repo; a repo file overrides it",
        sep="|",
        shared=True,
    ),
    _r(
        "review.finalize_comment",
        "ORCH_REVIEW_FINALIZE_COMMENT",
        "bool",
        True,
        "After the approved findings are applied and pushed, the worker posts one PR comment with every finding. The global file sets it for every repo; a repo file overrides it",
        shared=True,
    ),
    _r(
        "legacy.scan",
        "ORCH_LEGACY_SCAN",
        "bool",
        False,
        "Open PRs of this repo are scanned for added lines that legacy.rules count (LEGACY line)",
    ),
    _r(
        "legacy.rules",
        "ORCH_LEGACY_RULES",
        "tables",
        [],
        "Legacy rules ([[legacy.rules]]: kind, paths, optional pattern and exclude); the first rule that counts a line names its kind. No rules: nothing is scanned or mapped",
    ),
    _r(
        "legacy.exempt",
        "ORCH_LEGACY_EXEMPT",
        "list",
        [],
        "Path globs no legacy rule counts",
    ),
    _r(
        "focus.pattern",
        "ORCH_FOCUS_PATTERN",
        "str",
        "",
        "Case-insensitive regex matched against the labels of the workers of an architect working in this repo; empty turns the focus check off",
    ),
    _r(
        "focus.architect",
        "ORCH_FOCUS_ARCHITECT",
        "str",
        "",
        "Architect name never listed as lacking a focus worker",
    ),
    _r(
        "pi.deepseek",
        "ORCH_PI_DEEPSEEK",
        "bool",
        False,
        "pi workers may run on the DeepSeek API in this repo; off unless set to true in the personal .orchestration.local.toml (a committed .orchestration.toml cannot turn it on); the pi start step refuses DeepSeek otherwise",
        local_only=True,
    ),
]

REMOVED_KEYS = {
    "legacy.extra_pattern": "[[legacy.rules]]",
    "legacy.exempt_paths": "legacy.exempt",
}

BY_KEY = {s.key: s for s in SETTINGS}
BY_ENV = {s.env: s for s in SETTINGS}


DROPPED = {
    "pr.repos": "ORCH_PR_REPOS",
    "audit.git_watch_repos": "ORCH_GIT_WATCH_REPOS",
    "focus.skip_architects": "ORCH_FOCUS_SKIP_ARCHITECTS",
}


class ConfigError(ValueError):
    pass


def list_problem(setting, items):
    """Why items break the setting's choices, required entries or nonempty rule, else None."""
    if setting.nonempty and not items:
        return f"must name at least one of {', '.join(setting.choices)}"
    if setting.choices is not None:
        unknown = [item for item in items if item not in setting.choices]
        if unknown:
            return f"unknown entry {json.dumps(unknown[0])}; known: {', '.join(setting.choices)}"
    missing = [item for item in setting.required if item not in items]
    if items and missing:
        return f"must include {json.dumps(missing[0])}" + (
            f" ({setting.why})" if setting.why else ""
        )
    return None


def filled(value):
    return bool(value) and not (value.startswith("<") and value.endswith(">"))


def enabled_providers(values):
    """The provider names resolved ORCH_* values turn on, in PROVIDERS order."""
    setting = BY_KEY["providers.enabled"]
    text = values.get(setting.env, "")
    if not text:
        return [
            p.name
            for p in PROVIDERS
            if p.need != "none" or any(filled(values.get(m, "")) for m in p.models)
        ]
    names = text.split(setting.sep)
    problem = list_problem(setting, names)
    if problem:
        raise ConfigError(f"{setting.key} ({setting.env}): {problem}")
    return [p.name for p in PROVIDERS if p.name in names]


def claude_tiers(values):
    """The Claude worker tiers resolved ORCH_* values allow, in CLAUDE_TIERS order."""
    setting = BY_KEY["providers.claude"]
    text = values.get(setting.env, "")
    names = text.split(setting.sep) if text else []
    problem = list_problem(setting, names)
    if problem:
        raise ConfigError(f"{setting.key} ({setting.env}): {problem}")
    return [t for t in CLAUDE_TIERS if t in names]


def required_models(values):
    """Model settings that must be filled: every model of an enabled "all" provider; of a "one" provider, its filled models, or all when none is."""
    enabled = enabled_providers(values)
    needed = []
    for p in PROVIDERS:
        if p.name not in enabled or p.need == "none":
            continue
        set_models = [m for m in p.models if filled(values.get(m, ""))]
        needed += set_models if p.need == "one" and set_models else p.models
    return needed


def tier_model(values, env):
    """The model a tier runs on: its own setting, else, for a "one" provider, its first filled model; empty when none."""
    if filled(values.get(env, "")):
        return values[env]
    provider = next(p for p in PROVIDERS if env in p.models)
    if provider.need != "one":
        return ""
    return next((values[m] for m in provider.models if filled(values.get(m, ""))), "")


def printed(values):
    """Resolved values as orch-settings prints them: an unset tier of a "one" provider shows the model that serves it."""
    shown = dict(values)
    for p in PROVIDERS:
        if p.need == "one":
            for m in p.models:
                shown[m] = tier_model(values, m) or values.get(m, "")
    return shown


def fill_models(values):
    """The required model settings without a usable built-in default: the ones the user has to fill in."""
    return [m for m in required_models(values) if not filled(BY_ENV[m].default)]


def scoped(scope):
    return [s for s in SETTINGS if s.scope == scope]


def listed(scope):
    """The settings a scope's example and doc table show: its own, and the repo ones the global file also accepts."""
    return [s for s in SETTINGS if s.scope == scope or (scope == "global" and s.shared)]


def env_names():
    return [s.env for s in SETTINGS]


def expand(text, setting):
    if setting.paths and (text == "~" or text.startswith("~/")):
        return str(Path.home()) + text[1:]
    return text


def check_type(setting, value):
    kind = setting.kind
    if kind == "str":
        return isinstance(value, str)
    if kind == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "bool":
        return isinstance(value, bool)
    if kind == "list":
        return isinstance(value, list) and all(isinstance(item, str) for item in value)
    if kind == "tables":
        return isinstance(value, list) and all(isinstance(item, dict) for item in value)
    return isinstance(value, (list, dict))


TYPE_NAMES = {
    "str": "a string",
    "int": "an integer",
    "number": "a number",
    "bool": "true or false",
    "list": "a list of strings",
    "json": "an array or table",
    "tables": "an array of tables",
}


def env_value(setting, value):
    """The ORCH_* string a TOML value stands for."""
    kind = setting.kind
    if kind == "bool":
        return "1" if value else "0"
    if kind in ("int", "number"):
        return repr(value) if isinstance(value, float) else str(value)
    if kind == "list":
        return setting.sep.join(expand(item, setting) for item in value)
    if kind in ("json", "tables"):
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    return expand(value, setting)


def parse(text, label):
    if tomllib is None:
        raise ConfigError(
            f"{label}: reading TOML needs Python 3.11 or newer (this is {sys.version.split()[0]})"
        )
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{label}: invalid TOML: {error}") from None


def has_nul(value):
    if isinstance(value, str):
        return "\0" in value
    if isinstance(value, dict):
        return any(has_nul(k) or has_nul(v) for k, v in value.items())
    return isinstance(value, list) and any(map(has_nul, value))


def validate(data, label, scope):
    """{setting: value} for a parsed file; every key must be a known setting of this scope."""
    found = {}
    for table, entries in data.items():
        if not isinstance(entries, dict):
            raise ConfigError(
                f"{label}: {table}: unknown key (settings live in tables such as [pr] or [watchers])"
            )
        for name, value in entries.items():
            key = f"{table}.{name}"
            setting = BY_KEY.get(key)
            if setting is None and key in DROPPED:
                warn(
                    f"{label}: {key}: not read (repos and architects come from the open work); run install.sh to drop it"
                )
                continue
            if key in REMOVED_KEYS:
                raise ConfigError(
                    f"{label}: {key}: was removed; run install.sh to convert it to {REMOVED_KEYS[key]}"
                )
            if setting is None:
                raise ConfigError(f"{label}: {key}: unknown key")
            if setting.scope != scope and not (setting.shared and scope == "global"):
                where = (
                    f"a repo's {REPO_FILE} or {LOCAL_FILE}"
                    if setting.scope == "repo"
                    else f"the global {GLOBAL_FILE}"
                )
                raise ConfigError(
                    f"{label}: {key}: is a {setting.scope} setting; set it in {where}"
                )
            if not check_type(setting, value):
                raise ConfigError(
                    f"{label}: {key}: must be {TYPE_NAMES[setting.kind]}, got {type(value).__name__}"
                )
            if has_nul(value):
                raise ConfigError(f"{label}: {key}: contains a NUL character")
            if setting.kind == "list":
                for item in value:
                    if (
                        re.search(r"\s", item)
                        if setting.sep == " "
                        else setting.sep in item
                    ) or item == "":
                        what = (
                            "whitespace" if setting.sep == " " else f'"{setting.sep}"'
                        )
                        raise ConfigError(
                            f"{label}: {key}: entry {json.dumps(item)} is empty or contains {what}, "
                            "which separates entries"
                        )
                problem = list_problem(setting, value)
                if problem:
                    raise ConfigError(f"{label}: {key}: {problem}")
            found[setting] = value
    return found


def read_file(path, scope):
    label = os.fspath(path)
    try:
        text = Path(path).read_text()
    except (OSError, UnicodeDecodeError) as error:
        raise ConfigError(f"{label}: cannot read: {error}") from None
    return validate(parse(text, label), label, scope)


def as_env(values):
    return {setting.env: env_value(setting, value) for setting, value in values.items()}


def resolved_global(path, environ):
    """Every global ORCH_* setting as the scripts see it: default < the file < an exported variable."""
    file = {**load_global(path), **as_env(shared_values(path))}
    values = {
        s.env: environ.get(s.env, file.get(s.env, env_value(s, s.default)))
        for s in listed("global")
    }
    values[BY_KEY["providers.enabled"].env] = ",".join(enabled_providers(values))
    values[BY_KEY["providers.claude"].env] = ",".join(claude_tiers(values))
    return values


def config_path(environ):
    """The global file a script reads: ORCH_CONFIG (must be a regular file), else ~/.claude/orchestration.toml."""
    chosen = environ.get("ORCH_CONFIG")
    if chosen and not Path(chosen).is_file():
        raise ConfigError(f"ORCH_CONFIG is not a regular file: {chosen}")
    return chosen or str(Path(GLOBAL_FILE).expanduser())


def print_lines(values):
    """One NAME=value line per setting; a control character in a value would split or hide a line."""
    for name, value in values.items():
        if re.search(r"[\x00-\x08\x0a-\x1f\x7f]", value):
            raise ConfigError(
                f"{name}: the value contains a control character; one setting must stay on one line"
            )
    return "".join(f"{name}={value}\n" for name, value in values.items())


def print_path(args, environ):
    """The global file to print: an explicit existing file, else config_path."""
    if not args:
        return config_path(environ)
    if len(args) > 1 or args[0].startswith("-") or not Path(args[0]).is_file():
        raise ConfigError(
            f"print: expected no argument or one existing settings file, got {' '.join(args)}"
        )
    return args[0]


def load_global(path):
    """ORCH_* values the global file sets; a missing file sets none."""
    if not Path(path).exists():
        return {}
    return as_env({s: v for s, v in read_file(path, "global").items() if not s.shared})


def shared_values(path):
    """Shared settings the global file sets; a missing file sets none."""
    if not Path(path).is_file():
        return {}
    return {s: v for s, v in read_file(path, "global").items() if s.shared}


def global_layer(environ):
    """The shared settings of the selected global file; they sit under every repo layer."""
    return shared_values(config_path(environ))


def repo_root(start):
    path = Path(start).resolve()
    if path.is_file():
        path = path.parent
    for candidate in (path, *path.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def local_tracked(root):
    """True when git tracks the personal file, False when it does not (exit 1), None when git cannot tell."""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--error-unmatch", "--", LOCAL_FILE],
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return {0: True, 1: False}.get(result.returncode)


def warn(message):
    print(f"WARNING: {message}", file=sys.stderr)


def local_values(root):
    """Settings of a clone's personal file; warns when git tracks it, and then drops the personal-only settings."""
    local = Path(root) / LOCAL_FILE
    if not local.is_file():
        return {}
    tracked = local_tracked(root)
    if tracked is False:
        return read_file(local, "repo")
    if tracked:
        warn(
            f"{local} is tracked by git; it is personal. Run: git -C {shlex.quote(str(root))} rm --cached {LOCAL_FILE} "
            f"and add {LOCAL_FILE} to .gitignore"
        )
    else:
        warn(
            f"{local}: git could not say whether it is tracked; it is treated as tracked"
        )
    return committed_layer(read_file(local, "repo"), os.fspath(local))


def main_checkout(root):
    """The main checkout of a linked worktree, else None."""
    try:
        common = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "rev-parse",
                "--path-format=absolute",
                "--git-common-dir",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    main = Path(common).parent if common and Path(common).name == ".git" else None
    return main if main is not None and main.resolve() != Path(root).resolve() else None


def committed_layer(values, label):
    """A committed file's values without the personal-only settings; a committed file would opt in every clone."""
    for setting in [setting for setting in values if setting.local_only]:
        warn(
            f"{label}: {setting.key}: not read from a committed file; set it in {LOCAL_FILE}"
        )
        del values[setting]
    return values


def merged(layers, environ):
    """Built-in default < each layer in order < an exported variable, as ORCH_* strings."""
    result = {s.env: env_value(s, s.default) for s in scoped("repo")}
    for layer in [global_layer(environ), *layers]:
        result.update(as_env(layer))
    for name in result:
        if name in environ:
            result[name] = environ[name]
    return result


def load_repo(start, environ=None):
    """Repo settings for the checkout containing start: committed file, the main checkout's personal file, its own."""
    environ = os.environ if environ is None else environ
    root = repo_root(start)
    layers = []
    if root is not None:
        committed = root / REPO_FILE
        if committed.is_file():
            layers.append(
                committed_layer(read_file(committed, "repo"), os.fspath(committed))
            )
        main = main_checkout(root)
        if main is not None:
            layers.append(local_values(main))
        layers.append(local_values(root))
    return merged(layers, environ)


def load_remote(repo, ref, committed_text, clone=None, environ=None):
    """Repo settings for a PR: the committed file of its base ref, then the personal file of the user's clone."""
    environ = os.environ if environ is None else environ
    layers = []
    if committed_text:
        label = f"{repo}@{ref}:{REPO_FILE}"
        layers.append(
            committed_layer(
                validate(parse(committed_text, label), label, "repo"), label
            )
        )
    if clone:
        main = main_checkout(clone)
        if main is not None:
            layers.append(local_values(main))
        layers.append(local_values(clone))
    return merged(layers, environ)


GITHUB_REMOTE = re.compile(
    r"^(?:[a-z][a-z0-9+.-]*://(?:[^@/\s]+@)?github\.com(?::\d+)?/|(?:[^@/\s]+@)?github\.com:/?)"
    r"([^/\s]+/[^/\s]+?)(?:\.git)?/?$",
    re.I,
)


NO_ORIGIN = re.compile(
    r"No such remote|cannot change to .*: No such file or directory", re.I
)


def remote_matches(path, repo, remote):
    """True when the clone's GitHub remote is repo; raises CalledProcessError when the remote cannot be read."""
    args = ["git", "-C", str(path), "remote", "get-url", remote]
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=5,
        env=dict(os.environ, LC_ALL="C"),
    )
    if result.returncode:
        if NO_ORIGIN.search(result.stderr):
            return False
        raise subprocess.CalledProcessError(
            result.returncode, args, result.stdout, result.stderr
        )
    match = GITHUB_REMOTE.search(result.stdout.strip())
    return match is not None and match[1].lower() == repo.lower()


def matching_remote(path, repo):
    """'origin' or 'upstream' (the base repo of a fork), whichever is the clone's GitHub remote for repo, else None;
    raises CalledProcessError when a remote cannot be read."""
    for remote in ("origin", "upstream"):
        if remote_matches(path, repo, remote):
            return remote
    return None


def origin_matches(path, repo):
    """True when the clone's GitHub origin or upstream is repo; raises CalledProcessError when a remote cannot be read (see matching_remote)."""
    return matching_remote(path, repo) is not None


def known_clone(repo, clones):
    """The clone among clones whose origin is repo, else the first whose upstream is, else None."""
    for remote in ("origin", "upstream"):
        for path in clones:
            if remote_matches(path, repo, remote):
                return path
    return None


# --- Writing TOML ---


def toml_string(text):
    return json.dumps(text, ensure_ascii=False)


def toml_value(value, inline=False):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return toml_string(value)
    if isinstance(value, dict):
        return (
            "{ "
            + ", ".join(
                f"{toml_key(k)} = {toml_value(v, True)}" for k, v in value.items()
            )
            + " }"
        )
    items = [toml_value(item, inline) for item in value]
    if not inline and (
        any(isinstance(item, dict) for item in value) or len(", ".join(items)) > 80
    ):
        return "[\n" + "".join(f"  {item},\n" for item in items) + "]"
    return "[" + ", ".join(items) + "]"


def toml_key(name):
    return name if re.fullmatch(r"[A-Za-z0-9_-]+", name) else toml_string(name)


def render(values, comments=None, header=""):
    """TOML text for {setting: value}, grouped by table in schema order."""
    comments = comments or {}
    out, table = [header] if header else [], None
    for setting in SETTINGS:
        if setting not in values:
            continue
        if setting.table != table:
            if table is not None:
                out.append("")
            table = setting.table
            out.append(f"[{table}]")
        for line in comments.get(setting, []):
            out.append(f"# {line}" if line else "#")
        out.append(f"{setting.name} = {toml_value(values[setting])}")
    return "\n".join(out) + "\n"


def example(scope):
    """The generated part of a config example; array-of-tables settings are left to the example's own comments."""
    lines = {s: [s.doc] for s in listed(scope)}
    values = {
        s: s.default for s in listed(scope) if s.kind != "tables" and not s.local_only
    }
    if scope == "global":
        header = (
            "# Global settings for the Coordinator / architect setup. install.sh copies this file to\n"
            f"# {GLOBAL_FILE} on the first run only and never overwrites it.\n"
            "# An exported ORCH_* variable wins over this file; ORCH_CONFIG=/path selects another file.\n"
            f"# Repo settings ([guard], [legacy] scan, [focus] pattern, ...) live in each repo: {REPO_FILE}\n"
            f"# (committed) and {LOCAL_FILE} (personal, git-ignored). See docs/settings.md.\n"
        )
    else:
        header = (
            f"# Repo settings. Commit this as {REPO_FILE} at the repo root; personal overrides go in\n"
            f"# {LOCAL_FILE} next to it (git-ignored). An exported ORCH_* variable wins over both.\n"
        )
    return render(values, lines, header)


def markdown_default(setting):
    value = setting.default
    if value in ("", [], False) and setting.kind != "bool":
        return "empty"
    return f"`{toml_value(value, True)}`"


def markdown_table(scope):
    rows = ["| Key | Variable | Default | Meaning |", "|---|---|---|---|"]
    for setting in listed(scope):
        doc = setting.doc.replace("|", "\\|")
        rows.append(
            f"| `{setting.key}` | `{setting.env}` | {markdown_default(setting).replace('|', chr(92) + '|')} | {doc} |"
        )
    return "\n".join(rows)


# --- Migration from orchestration.env ---

OLD_NAMES = {
    "ORCH_V2_LAUNCHD": "ORCH_WATCHERS_SERVICE",
    "ORCH_V2_PR_SCOPE": "ORCH_WATCHERS_PR_SCOPE",
    "ORCH_V2_WORKER_MODEL": "ORCH_WATCHERS_WORKER_MODEL",
    "ORCH_V2_PRIMARY_REPO": "ORCH_WATCHERS_PRIMARY_REPO",
}
REMOVED = {
    "ORCH_BIG_REPO_REMOTES",
    "ORCH_LEGACY_REPOS",
    "ORCH_LEGACY_EXTRA_PATTERN",
    "ORCH_LEGACY_EXEMPT_PATHS",
    "ORCH_PR_TABS_TOPICS",
    *DROPPED.values(),
}
ASSIGN = re.compile(r"^\s*(?:export\s+)?(ORCH_[A-Z0-9_]*)=")


def env_file_values(path):
    """ORCH_* values bash gets from sourcing the old file, in a clean environment."""
    script = (
        'set +u; . "$1" >/dev/null 2>&1; shift; for n in "$@"; do [ -z "${!n+x}" ] || printf "%s\\0%s\\0" "$n" "${!n}"; done; '
        'printf "%s\\0" "$0"'
    )
    names = sorted(
        set(env_names()) | REMOVED | set(OLD_NAMES) | set(assigned_names(path))
    )
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in names and key not in ("BASH_ENV", "ENV")
    }
    syntax = subprocess.run(
        ["/bin/bash", "-n", str(path)], capture_output=True, env=env
    )
    if syntax.returncode:
        raise ConfigError(
            f"{path}: bash syntax error: {syntax.stderr.decode(errors='replace').strip()}"
        )
    done = "ORCH_ENV_DONE"
    out = subprocess.run(
        ["/bin/bash", "-c", script, done, str(path), *names],
        capture_output=True,
        env=env,
    ).stdout.decode()
    parts = out.split("\0")[:-1]
    if not parts or parts[-1] != done:
        raise ConfigError(
            f"{path}: stops before its end (exit or return in the file); nothing was converted"
        )
    parts = parts[:-1]
    return dict(zip(parts[::2], parts[1::2]))


def assigned_names(path):
    return [
        m[1]
        for m in map(ASSIGN.match, Path(path).read_text(errors="replace").splitlines())
        if m
    ]


def env_comments(path):
    """Comment lines directly above each assignment, without --- section rules."""
    comments, block = {}, []
    for line in Path(path).read_text(errors="replace").splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            text = stripped[1:].strip()
            if not text.startswith("---"):
                block.append(text)
            continue
        match = ASSIGN.match(line)
        if match:
            comments.setdefault(match[1], block)
        block = []
    return comments


def from_env(setting, text):
    """TOML value for an old ORCH_* string; None when the old value is empty for a typed setting."""
    kind = setting.kind
    if kind == "str":
        return text
    if text == "":
        return [] if kind == "list" else None
    if kind == "bool":
        if text not in ("0", "1"):
            raise ValueError(f'{setting.env}="{text}" is not 0 or 1')
        return text == "1"
    if kind == "int":
        return int(text)
    if kind == "number":
        return float(text) if re.search(r"[.eE]", text) else int(text)
    if kind == "list":
        return (
            text.split()
            if setting.sep == " "
            else [item for item in text.split(setting.sep) if item]
        )
    return json.loads(text)


NO_BUILTIN_RULES = 'legacy.scan has no built-in rules; add the [[legacy.rules]] this repo needs (docs/settings.md, "Legacy rules")'


def rg_problem(pattern, rg=None):
    """Why rg rejects pattern as a regex, or None."""
    rg = rg or shutil.which("rg")
    if rg is None:
        return "rg (ripgrep) is not on PATH"
    result = subprocess.run(
        [rg, "--no-config", "--regexp", pattern, "-"],
        input=b"",
        capture_output=True,
        timeout=30,
    )
    return (
        None
        if result.returncode in (0, 1)
        else " ".join(result.stderr.decode(errors="replace").split())
    )


def legacy_rule(pattern):
    return {"kind": "LEGACY", "pattern": pattern, "paths": ["**"]}


def legacy_layer(pattern, exempt):
    """legacy.rules and legacy.exempt for the removed extra pattern and exempt paths."""
    layer = {}
    if pattern:
        layer[BY_KEY["legacy.rules"]] = [legacy_rule(pattern)]
    if exempt:
        layer[BY_KEY["legacy.exempt"]] = list(exempt)
    return layer


def repo_layers(values, report):
    """{owner/name: {setting: value}} the old global values meant for each repo."""

    def repo_setting(key, env):
        setting = BY_KEY[key]
        if env not in values:
            return None
        try:
            value = from_env(setting, values[env])
        except ValueError as error:
            report.append(f"not converted: {error}")
            return None
        return value if value not in (None, [], "") else None

    repos = {}
    big = values.get("ORCH_BIG_REPO_REMOTES", "").split()
    legacy = values.get("ORCH_LEGACY_REPOS", "").split()
    for repo in big:
        repos.setdefault(repo, {})[BY_KEY["guard.big_repo"]] = True
    for repo in legacy:
        layer = repos.setdefault(repo, {})
        layer[BY_KEY["legacy.scan"]] = True
        pattern = values.get("ORCH_LEGACY_EXTRA_PATTERN", "")
        problem = rg_problem(pattern) if pattern else None
        if problem:
            report.append(
                f"not converted: ORCH_LEGACY_EXTRA_PATTERN for {repo} is not an rg regex ({problem}); "
                "add it as a [[legacy.rules]] pattern by hand"
            )
            pattern = ""
        layer.update(
            legacy_layer(pattern, values.get("ORCH_LEGACY_EXEMPT_PATHS", "").split())
        )
        report.append(f"{repo}: {NO_BUILTIN_RULES}")
    gates = repo_setting("audit.merge_gate_checks", "ORCH_MERGE_GATE_CHECKS")
    if gates is not None:
        for repo in values.get("ORCH_PR_REPOS", "").split():
            repos.setdefault(repo, {})[BY_KEY["audit.merge_gate_checks"]] = gates
    focus = {
        key: repo_setting(key, env)
        for key, env in (
            ("focus.pattern", "ORCH_FOCUS_PATTERN"),
            ("focus.architect", "ORCH_FOCUS_ARCHITECT"),
        )
    }
    focus = {key: value for key, value in focus.items() if value is not None}
    if focus:
        primary = values.get("ORCH_WATCHERS_PRIMARY_REPO", "")
        targets = (
            [primary]
            if primary
            else sorted(repos) or values.get("ORCH_PR_REPOS", "").split()[:1]
        )
        for repo in targets:
            layer = repos.setdefault(repo, {})
            layer.update({BY_KEY[key]: value for key, value in focus.items()})
        if not targets:
            report.append(
                "not converted: focus.pattern and focus.architect (no repo to put them in)"
            )
    for env in ("ORCH_LEGACY_EXTRA_PATTERN", "ORCH_LEGACY_EXEMPT_PATHS"):
        if values.get(env) and not legacy:
            report.append(
                f"not converted: {env} (no ORCH_LEGACY_REPOS repo to put it in)"
            )
    return repos


LEGACY_KEY = re.compile(
    r'^\s*(?:(?:"legacy"|\'legacy\'|legacy)\s*\.\s*)?(?:"|\')?(extra_pattern|exempt_paths)(?:"|\')?\s*='
)
TABLE = re.compile(r"^\s*\[\[?\s*([^\]]*?)\s*\]\]?\s*(#.*)?$")


def upgrade(path):
    """Rewrites legacy.extra_pattern and legacy.exempt_paths of a repo settings file as rules; report lines."""
    label = os.fspath(path)
    text = Path(path).read_text()
    legacy = parse(text, label).get("legacy")
    old = (
        {key: legacy[key] for key in ("extra_pattern", "exempt_paths") if key in legacy}
        if isinstance(legacy, dict)
        else {}
    )
    if not old:
        return []
    pattern, exempt = old.get("extra_pattern", ""), old.get("exempt_paths", [])
    if not isinstance(pattern, str) or not check_type(BY_KEY["legacy.exempt"], exempt):
        raise ConfigError(
            f"{label}: legacy.extra_pattern must be a string and legacy.exempt_paths a list of strings; edit it by hand"
        )
    problem = rg_problem(pattern) if pattern else None
    if problem:
        raise ConfigError(
            f"{label}: legacy.extra_pattern is not an rg regex ({problem}); rewrite it as a [[legacy.rules]] pattern by hand"
        )
    lines, table = [], None
    for line in text.splitlines():
        header = TABLE.match(line)
        if header:
            table = re.sub(r"\s*\.\s*", ".", re.sub(r'["\']', "", header[1]))
        match = LEGACY_KEY.match(line)
        dotted = match is not None and line.lstrip().startswith(
            ("legacy", '"legacy', "'legacy")
        )
        if match and ((table == "legacy" and not dotted) or (table is None and dotted)):
            if match[1] == "exempt_paths":
                lines.append(line[: match.start(1)] + "exempt" + line[match.end(1) :])
            elif line[match.end() :].lstrip()[:3] in ('"""', "'''"):
                raise ConfigError(
                    f"{label}: legacy.extra_pattern spans several lines; edit it by hand"
                )
            continue
        lines.append(line)
    if pattern:
        lines += ["", "[[legacy.rules]]"] + [
            f"{key} = {toml_value(value)}"
            for key, value in legacy_rule(pattern).items()
        ]
    out = "\n".join(lines) + "\n"
    expected = parse(text, label)
    for key in old:
        del expected["legacy"][key]
    if "exempt_paths" in old:
        expected["legacy"]["exempt"] = exempt
    if pattern:
        expected["legacy"]["rules"] = expected["legacy"].get("rules", []) + [
            legacy_rule(pattern)
        ]

    def settings(data):
        return {
            table: entries
            for table, entries in data.items()
            if not (table == "legacy" and entries == {})
        }

    if settings(parse(out, label)) != settings(expected):
        raise ConfigError(
            f"{label}: legacy.extra_pattern or legacy.exempt_paths cannot be converted in place; edit it by hand"
        )
    written = validate(parse(out, label), label, "repo")
    Path(path).write_text(out)
    report = [
        f"converted {label}: "
        + ", ".join(
            part
            for part, present in (
                (
                    "legacy.extra_pattern -> [[legacy.rules]] (kind LEGACY, every path)",
                    "extra_pattern" in old,
                ),
                ("legacy.exempt_paths -> legacy.exempt", "exempt_paths" in old),
            )
            if present
        )
    ]
    if written.get(BY_KEY["legacy.scan"]):
        report.append(f"{label}: {NO_BUILTIN_RULES}")
    return report


def configured_clones(path):
    """The audit.git_watch_repos clones a global file still lists before install.sh drops the key."""
    try:
        data = parse(Path(path).read_text(), os.fspath(path))
    except (OSError, UnicodeDecodeError, ConfigError):
        return []
    audit = data.get("audit")
    value = audit.get("git_watch_repos", []) if isinstance(audit, dict) else []
    if not isinstance(value, list):
        return []
    return [
        str(Path.home()) + item[1:] if item == "~" or item.startswith("~/") else item
        for item in value
        if isinstance(item, str) and item
    ]


def upgrade_targets(files, clones=()):
    """Repo files to upgrade: each of files, and for each clone folder its personal file and its main checkout's."""
    candidates, targets, seen = [Path(path) for path in files], [], set()
    for clone in map(Path, clones):
        if clone.is_dir():
            main = main_checkout(clone)
            candidates += [clone / LOCAL_FILE] + (
                [main / LOCAL_FILE] if main is not None else []
            )
    for candidate in candidates:
        if candidate.is_file() and candidate.resolve() not in seen:
            seen.add(candidate.resolve())
            targets.append(candidate)
    return targets


def git(path, *args):
    return subprocess.run(
        ["git", "-C", str(path), *args], capture_output=True, text=True, timeout=10
    )


def exclude_local(clone):
    """Adds the personal file to the clone's info/exclude unless git already ignores it; True when added."""
    if git(clone, "check-ignore", "-q", LOCAL_FILE).returncode == 0:
        return False
    exclude = (
        Path(clone)
        / git(clone, "rev-parse", "--git-path", "info/exclude").stdout.strip()
    )
    exclude.parent.mkdir(parents=True, exist_ok=True)
    text = exclude.read_text() if exclude.exists() else ""
    if text and not text.endswith("\n"):
        text += "\n"
    exclude.write_text(text + LOCAL_FILE + "\n")
    return True


def write_repo_layer(repo, layer, clone, snippet_dir, report):
    body = render(
        layer, header=f"# Repo settings for {repo}, converted from orchestration.env.\n"
    )
    if clone:
        local = os.path.join(clone, LOCAL_FILE)
        if not os.path.exists(local):
            Path(local).write_text(body)
            report.append(f"created {local} ({repo})")
            if exclude_local(clone):
                report.append(f"added {LOCAL_FILE} to the info/exclude of {clone}")
            return
        try:
            existing = read_file(local, "repo")
        except ConfigError as error:
            existing, problem = None, str(error)
        if existing is not None and all(existing.get(s) == v for s, v in layer.items()):
            report.append(f"kept {local} (already has the converted {repo} settings)")
            return
        report.append(
            f"kept {local} (yours, not merged{': ' + problem if existing is None else ''}); "
            "merge the snippet below by hand"
        )
    owner, name = repo.split("/", 1)
    target = os.path.join(snippet_dir, owner, name + ".toml")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    Path(target).write_text(body)
    report.append(
        f"wrote {target}: commit it as {REPO_FILE} in {repo}, or save it as {LOCAL_FILE} in your clone"
    )


def migrate(env_path, toml_path, snippet_dir):
    """Converts the old file: global values to toml_path, repo values to clones or snippets. Returns report lines."""
    values = env_file_values(env_path)
    comments = env_comments(env_path)
    report, global_values = [], {}
    for old, new in OLD_NAMES.items():
        if old in values and new not in values:
            values[new] = values[old]
            report.append(f"renamed {old} to {new}")
    for setting in scoped("global"):
        if setting.env not in values:
            continue
        try:
            value = from_env(setting, values[setting.env])
        except ValueError as error:
            report.append(f"not converted: {setting.env}: {error}")
            continue
        if value is None:
            report.append(
                f"left out {setting.key} ({setting.env} was empty; the default applies)"
            )
            continue
        global_values[setting] = value
    unknown = sorted(
        n
        for n in assigned_names(env_path)
        if n not in BY_ENV and n not in REMOVED and n not in OLD_NAMES
    )
    if unknown:
        report.append("not converted (unknown keys): " + " ".join(unknown))
    header = (
        "# Global orchestration settings, converted from orchestration.env. See docs/settings.md.\n"
        "# An exported ORCH_* variable wins over this file.\n"
    )
    text = render(
        global_values, {s: comments.get(s.env, []) for s in global_values}, header
    )
    validate(parse(text, str(toml_path)), str(toml_path), "global")
    clones = values.get("ORCH_GIT_WATCH_REPOS", "").split()
    layers = sorted(repo_layers(values, report).items())
    for repo, layer in layers:
        label = f"{repo} repo settings"
        validate(parse(render(layer), label), label, "repo")
    for repo, layer in layers:
        write_repo_layer(repo, layer, known_clone(repo, clones), snippet_dir, report)
    Path(toml_path).write_text(text)
    return [f"converted {env_path} -> {toml_path}"] + report


def set_key(path, env, text):
    """Sets one global setting in the file from its ORCH_* string form, keeping every other line."""
    setting = BY_ENV[env]
    value = from_env(setting, text)
    value = [] if value is None else value
    rendered = toml_value(value)
    lines = Path(path).read_text().splitlines()

    def name(part):
        return rf'(?:"{part}"|\'{part}\'|{part})'

    header = re.compile(r'^\s*\[\s*("?)([A-Za-z0-9_-]+)\1\s*\]\s*(#.*)?$')
    plain = re.compile(rf"^\s*{name(setting.name)}\s*=")
    dotted = re.compile(rf"^\s*{name(setting.table)}\s*\.\s*{name(setting.name)}\s*=")
    dotted_table = re.compile(rf"^\s*{name(setting.table)}\s*\.")
    table, start, done, last_dotted = None, None, False, None
    for index, current in enumerate(lines):
        match = header.match(current)
        if match:
            table = match[2]
            start = index if table == setting.table else start
            continue
        found = (
            plain.match(current)
            if table == setting.table
            else dotted.match(current)
            if table is None
            else None
        )
        if found:
            if current.rstrip().endswith(("[", "{")):
                raise ConfigError(
                    f"{path}: {setting.key}: spans several lines; edit it by hand"
                )
            key = (
                setting.name
                if table == setting.table
                else f"{setting.table}.{setting.name}"
            )
            lines[index], done = f"{key} = {rendered}", True
            break
        if table is None and dotted_table.match(current):
            last_dotted = index
    if not done and start is not None:
        lines.insert(start + 1, f"{setting.name} = {rendered}")
    elif not done and last_dotted is not None:
        lines.insert(last_dotted + 1, f"{setting.table}.{setting.name} = {rendered}")
    elif not done:
        lines += (
            ["", f"[{setting.table}]", f"{setting.name} = {rendered}"]
            if lines
            else [f"[{setting.table}]", f"{setting.name} = {rendered}"]
        )
    text_out = "\n".join(lines) + "\n"
    written = validate(parse(text_out, str(path)), str(path), "global")
    if written.get(setting) != value:
        raise ConfigError(
            f"{path}: {setting.key}: cannot be set in place; edit it by hand"
        )
    Path(path).write_text(text_out)


def drop_removed_keys(path):
    """Drop retired rules, including disjoint blocks and inline table members.

    Candidate edits are accepted only when TOML proves every other value is
    unchanged. This keeps formatting/comments without introducing a TOML writer.
    """
    path = Path(path)
    with path.open(encoding="utf-8", newline="") as source:
        text = source.read()
    data = parse(text, str(path))
    if not isinstance(data.get("pr_tabs"), dict) or "topics" not in data["pr_tabs"]:
        return False

    def without_topics(value):
        result = dict(value)
        if isinstance(result.get("pr_tabs"), dict):
            tabs = dict(result["pr_tabs"])
            tabs.pop("topics", None)
            if tabs:
                result["pr_tabs"] = tabs
            else:
                result.pop("pr_tabs")
        return result

    expected = without_topics(data)
    validate(expected, str(path), "global")

    def ranges(source):
        lines = source.splitlines(keepends=True)
        offsets = [0]
        for line in lines:
            offsets.append(offsets[-1] + len(line))
        table = None
        for index, line in enumerate(lines):
            header = re.match(r"^\s*\[\s*['\"]?([\w-]+)['\"]?\s*\]\s*(?:#.*)?$", line)
            if header:
                table = header[1]
            plain = table == "pr_tabs" and re.match(r"^\s*['\"]?topics['\"]?\s*=", line)
            dotted = table is None and re.match(
                r"^\s*['\"]?pr_tabs['\"]?\s*\.\s*['\"]?topics['\"]?\s*=", line
            )
            block = re.match(
                r"^\s*\[\[?\s*['\"]?pr_tabs['\"]?\s*\.\s*['\"]?topics['\"]?\s*\]", line
            )
            if plain or dotted or block:
                for end in offsets[index + 1 :]:
                    start = index
                    if (
                        index
                        and lines[index - 1].rstrip("\r\n")
                        == "# Tab-group rules: tables with name, color and repo/title/any regex lists"
                    ):
                        start -= 1
                    yield offsets[start], end
        # Inline members can be first, middle or last, with nested arrays/tables.
        # The parser/comparison below rejects matches inside strings or comments.
        for match in re.finditer(r"(?<![\w.-])['\"]?topics['\"]?\s*=", source):
            start = match.start()
            previous = start - 1
            while previous >= 0 and source[previous].isspace():
                previous -= 1
            for end in range(match.end(), len(source)):
                if source[end] not in ",}\n":
                    continue
                yield start, end + (source[end] == ",")
                if previous >= 0 and source[previous] == ",":
                    yield previous, end

    while "topics" in data.get("pr_tabs", {}):
        size = len(json.dumps(data["pr_tabs"]["topics"]))
        for start, end in ranges(text):
            candidate = text[:start] + text[end:]
            try:
                parsed = parse(candidate, str(path))
            except ConfigError:
                continue
            remaining = parsed.get("pr_tabs", {}).get("topics")
            removed = "topics" not in parsed.get("pr_tabs", {})
            if without_topics(parsed) == expected and (
                removed or len(json.dumps(remaining)) < size
            ):
                text, data = candidate, parsed
                break
        else:
            raise ConfigError(f"{path}: cannot remove retired pr_tabs.topics in place")
    with path.open("w", encoding="utf-8", newline="") as destination:
        destination.write(text)
    return True


def bracket_depth(text):
    depth, quote, index = 0, None, 0
    while index < len(text):
        char = text[index]
        if quote:
            if char == "\\" and quote == '"':
                index += 1
            elif char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char == "#":
            break
        elif char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
        index += 1
    return depth


def tables(data, keep_dropped=True):
    """{table: entries} of parsed TOML without empty tables, and without the DROPPED keys unless keep_dropped."""
    found = {}
    for table, entries in data.items():
        if isinstance(entries, dict):
            entries = {
                name: value
                for name, value in entries.items()
                if keep_dropped or f"{table}.{name}" not in DROPPED
            }
            if entries:
                found[table] = entries
    return found


def drop_keys(path):
    """Removes the DROPPED keys and the comment lines directly above them; returns the removed keys."""
    text = Path(path).read_text()
    before = parse(text, str(path))
    present = [
        f"{table}.{name}"
        for table, entries in tables(before).items()
        for name in entries
        if f"{table}.{name}" in DROPPED
    ]
    if not present:
        return []
    problem = ConfigError(
        f"{path}: cannot drop {' '.join(present)} in place; edit it by hand"
    )
    lines, kept, removed, table, skip = text.splitlines(), [], [], None, 0
    header = re.compile(r'^\s*\[\s*(["\']?)([A-Za-z0-9_-]+)\1\s*\]\s*(#.*)?$')
    key = re.compile(
        r'^\s*(?:(["\']?)([A-Za-z0-9_-]+)\1\s*\.\s*)?(["\']?)([A-Za-z0-9_-]+)\3\s*=(.*)$'
    )
    for line in lines:
        if skip > 0:
            skip += bracket_depth(line)
            continue
        if match := header.match(line):
            table = match[2]
        elif (
            match := key.match(line)
        ) and f"{match[2] or table}.{match[4]}" in DROPPED:
            removed.append(f"{match[2] or table}.{match[4]}")
            while kept and kept[-1].lstrip().startswith("#"):
                kept.pop()
            skip = bracket_depth(match[5])
            continue
        kept.append(line)
    text_out = "\n".join(kept) + "\n"
    try:
        after = parse(text_out, str(path))
    except ConfigError:
        raise problem from None
    if tables(after) != tables(before, keep_dropped=False):
        raise problem
    Path(path).write_text(text_out)
    return removed


def file_keys(path, scope):
    return [s.env for s in read_file(path, scope)]


# --- Command line ---


def shell_lines(values):
    return "".join(
        f"orch_set_default {name} {shlex.quote(value)}\n"
        for name, value in values.items()
    )


def main(argv):
    try:
        command = argv[1] if len(argv) > 1 else ""
        if command == "global" and len(argv) == 3:
            sys.stdout.write(shell_lines(load_global(argv[2])))
        elif command == "print":
            sys.stdout.write(
                print_lines(
                    printed(
                        resolved_global(print_path(argv[2:], os.environ), os.environ)
                    )
                )
            )
        elif command == "repo" and len(argv) == 3:
            selected = {k: v for k, v in os.environ.items() if k == "ORCH_CONFIG"}
            sys.stdout.write(shell_lines(load_repo(argv[2], selected)))
        elif command == "migrate" and len(argv) == 5:
            print("\n".join(migrate(*argv[2:])))
        elif command == "upgrade" and len(argv) >= 3:
            args, watched = argv[2:], []
            if "--watched" in args:
                watched = configured_clones(args[args.index("--watched") + 1])
                args = (
                    args[: args.index("--watched")]
                    + args[args.index("--watched") + 2 :]
                )
            files, clones = (
                (args[: args.index("--clones")], args[args.index("--clones") + 1 :])
                if "--clones" in args
                else (args, [])
            )
            for path in upgrade_targets(files, clones + watched):
                for line in upgrade(path):
                    print(line)
        elif command == "example" and len(argv) == 3 and argv[2] in ("global", "repo"):
            sys.stdout.write(example(argv[2]))
        elif command == "table" and len(argv) == 3 and argv[2] in ("global", "repo"):
            print(markdown_table(argv[2]))
        elif command == "set" and len(argv) >= 5 and len(argv) % 2 == 1:
            for name, value in zip(argv[3::2], argv[4::2]):
                set_key(argv[2], name, value)
        elif command == "drop-removed" and len(argv) == 3:
            if drop_removed_keys(argv[2]):
                print("removed pr_tabs.topics (architect grouping is automatic)")
        elif command == "drop" and len(argv) == 3:
            print(" ".join(drop_keys(argv[2])))
        elif command == "keys" and len(argv) == 3:
            print(" ".join(file_keys(argv[2], "global")))
        elif command == "providers" and len(argv) <= 3:
            print(
                ",".join(
                    enabled_providers(
                        resolved_global(print_path(argv[2:], os.environ), os.environ)
                    )
                )
            )
        elif command == "fill-models" and len(argv) <= 3:
            print(
                " ".join(
                    fill_models(
                        resolved_global(print_path(argv[2:], os.environ), os.environ)
                    )
                )
            )
        elif command == "names" and len(argv) == 2:
            print(" ".join(env_names()))
        else:
            print(
                "usage: orch_settings.py global <file> | print [<file>] | repo <dir> | migrate <env> <toml> <snippet-dir> "
                "| upgrade [--watched <global-file>] <repo-file>... [--clones <dir>...] | set <file> <ORCH_NAME> <value>... | drop <file> | keys <file> | providers [<file>] | fill-models [<file>] "
                "| example global|repo | table global|repo | names",
                file=sys.stderr,
            )
            return 2
    except ConfigError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
