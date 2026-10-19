#!/bin/bash
# Prints one FOCUS line every interval: working workers per architect, idle architects, architects without a worker matching
# the focus.pattern of the repo the architect works in, heavy slots.
# Worker owner comes from the workspace token tokens.owner.
root="$(cd "$(dirname "$0")" && pwd)"
. "$root/coord-lib.sh"
orch_load_config
orch_export_config
unset ORCH_LOADER_PYTHON
orch_python >/dev/null || exit 2
INTERVAL=${1:-900}
while true; do
  ORCH_SETTINGS_LIB="$(dirname "$(orch_settings_module)")" "$ORCH_LOADER_PYTHON" - <<'PY'
import json,os,re,subprocess,sys
sys.path.insert(0,os.environ["ORCH_SETTINGS_LIB"])
import orch_settings
def herdr(*a):
    try: return json.loads(subprocess.run([os.environ.get("HERDR_BIN_PATH") or os.environ.get("ORCH_HERDR_BIN") or "herdr",*a],capture_output=True,text=True,timeout=30).stdout)["result"]
    except Exception: return None
pl=herdr("pane","list"); wl=herdr("workspace","list")
if not pl or not wl:
    print("FOCUS READFAIL herdr",flush=True); raise SystemExit
panes=pl["panes"]; wss={w["workspace_id"]:w for w in wl["workspaces"]}
arch={}; focus={}
for p in panes:
    t=p.get("terminal_title_stripped","")
    if t.startswith("Architect::") and p.get("tokens",{}).get("role")=="architect":
        arch[p["workspace_id"]]=(t.replace("Architect::",""),p.get("agent_status"))
        try:
            settings=orch_settings.load_repo(p["cwd"]) if p.get("cwd") else orch_settings.merged([],os.environ)
            focus[p["workspace_id"]]=(re.compile(settings["ORCH_FOCUS_PATTERN"],re.I) if settings["ORCH_FOCUS_PATTERN"] else None,settings["ORCH_FOCUS_ARCHITECT"])
        except (orch_settings.ConfigError,re.error) as error:
            print("FOCUS READFAIL ORCH_FOCUS_PATTERN %s" % error,flush=True); raise SystemExit
work={}; fwork={}
for p in panes:
    t=p.get("terminal_title_stripped","")
    if not p.get("agent") or p.get("tokens",{}).get("role") in ("architect","coordinator"): continue
    w=wss.get(p["workspace_id"],{})
    o=w.get("tokens",{}).get("owner")
    if not o: continue
    pattern=focus.get(o,(None,""))[0]
    if pattern and pattern.search(w.get("label","")): fwork[o]=fwork.get(o,0)+1
    if p.get("agent_status")=="working": work[o]=work.get(o,0)+1
skip=set(filter(None, os.environ.get("ORCH_FOCUS_SKIP_ARCHITECTS", "").split(",")))
live={ws:n for ws,(n,s) in arch.items() if n not in skip}
slot_dir=os.environ.get("ORCH_HEAVY_SLOTS_DIR") or os.path.join(os.path.expanduser("~"),".claude","heavy-slots")
def has_live_slot(name):
    prefix=name.lower().replace(":","")+"-"
    try: files=os.listdir(slot_dir)
    except OSError: return False
    for filename in files:
        if not filename.startswith(prefix): continue
        path=os.path.join(slot_dir,filename)
        if not os.path.isfile(path): continue
        try:
            with open(path) as slot: pid=int(slot.read().split()[0])
            if pid <= 0: continue
        except (IndexError,OSError,ValueError): continue
        try:
            os.kill(pid,0)
            return True
        except PermissionError: return True
        except (OSError,OverflowError): continue
    return False
idle=[n for ws,n in live.items() if work.get(ws,0)==0 and arch[ws][1]!="working" and not has_live_slot(n)]
nofocus=[n for ws,n in live.items() if focus[ws][0] and fwork.get(ws,0)==0 and n!=focus[ws][1]]
try:
    slots=sum(line.endswith(" live") for line in subprocess.run(["heavy-slot","list"],capture_output=True,text=True,timeout=30).stdout.splitlines())
except (OSError,subprocess.TimeoutExpired): slots="?"
print("FOCUS workers=%d focus_workers_live=%d slots=%s idle_no_worker=%s no_focus_worker=%s" % (sum(work.values()),sum(fwork.values()),slots,",".join(idle) or "none",",".join(nofocus) or "none"),flush=True)
PY
  sleep "$INTERVAL"
done
