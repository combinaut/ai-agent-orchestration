import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test_support import IsolatedTestCase
import watchers as w
from test_balance import stuck
from watcher_platform import LinuxBalance, MacBalance, balance_metrics


def own_ps(text):
    return "\n".join(row + " " + str(os.getuid()) for row in text.splitlines())


class BalanceFollowupTests(IsolatedTestCase):
    def fixture(self, root, ticks=100000, start=100, system=0):
        (root/'uptime').write_text('10000 0')
        (root/'meminfo').write_text('MemTotal: 1000 kB\nMemAvailable: 500 kB\n')
        (root/'loadavg').write_text('1 1 1')
        (root/'slots').mkdir(exist_ok=True)
        proc = root/'7'
        proc.mkdir(exist_ok=True)
        values = ['0'] * 22
        values[0], values[11], values[12], values[19], values[21] = 'R', str(ticks-system), str(system), str(start), '256'
        (proc/'stat').write_text('7 (worker) ' + ' '.join(values))
        (proc/'cwd').unlink(missing_ok=True)
        (proc/'cwd').symlink_to('/repo/worktrees/busy')
        return proc

    def test_mac_filters_foreign_processes_but_keeps_own_readfail(self):
        def command(*args):
            if args[0] == 'ps':
                if args[1:3] == ('-U', str(os.getuid())):
                    return own_ps('7 80 1024 R\n8 1 10 S') + '\n5 0 100 S ' + str(os.getuid()+1)
                return own_ps('1 0 100 S\n7 80 1024 R\n8 1 10 S')
            return 'p7\nn/repo/worktrees/busy'
        adapter = MacBalance()
        with patch('watcher_platform.process_alive', return_value=True):
            rows, errors = adapter.processes(command)
        self.assertEqual(errors, ['pid:8'])
        self.assertEqual(rows[0]['cpu'], 80)

    def test_linux_skips_foreign_pid_before_reading_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proc = self.fixture(root)
            foreign = root/'1'
            foreign.mkdir()
            (foreign/'stat').write_text('unreadable foreign process')
            original = Path.stat
            def stat(path, *args, **kwargs):
                info = original(path, *args, **kwargs)
                if path == foreign:
                    return SimpleNamespace(st_mode=info.st_mode, st_uid=os.getuid()+1)
                return info
            with patch.object(Path, 'stat', stat):
                raw = balance_metrics(None, w.BALANCE_LIMITS, root/'slots', LinuxBalance(root, ticks=100))
            self.assertEqual(raw['unreadable'], [])
            (proc/'cwd').unlink()
            with patch.object(Path, 'stat', stat):
                raw = balance_metrics(None, w.BALANCE_LIMITS, root/'slots', LinuxBalance(root, ticks=100))
            self.assertEqual(raw['unreadable'], ['pid:7'])

    def test_linux_delta_detects_spike_then_idle_after_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture(root)
            raw = balance_metrics(None, w.BALANCE_LIMITS, root/'slots', LinuxBalance(root, ticks=100))
            before, _ = w.reduce('balance', None, raw)
            self.fixture(root, ticks=100080, system=40)
            (root/'uptime').write_text('10001 0')
            raw = balance_metrics(None, w.BALANCE_LIMITS, root/'slots', LinuxBalance(root, ticks=100), before=before)
            self.assertEqual(raw['top'][0]['cpu'], 80)
            self.assertEqual(raw['heavy'], 1)
            before, _ = w.reduce('balance', before, raw)
            (root/'uptime').write_text('10002 0')
            raw = balance_metrics(None, w.BALANCE_LIMITS, root/'slots', LinuxBalance(root, ticks=100), before=before)
            self.assertEqual(raw['top'][0]['cpu'], 0)
            self.assertEqual(raw['heavy'], 0)
            before, _ = w.reduce('balance', before, raw)
            self.fixture(root, ticks=100240)
            (root/'uptime').write_text('10004 0')
            raw = balance_metrics(None, w.BALANCE_LIMITS, root/'slots', LinuxBalance(root, ticks=100), before=before)
            self.assertEqual(raw['top'][0]['cpu'], 80)

    def test_linux_pid_reuse_and_clock_reset_use_lifetime_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture(root)
            raw = balance_metrics(None, w.BALANCE_LIMITS, root/'slots', LinuxBalance(root, ticks=100))
            before, _ = w.reduce('balance', None, raw)
            self.fixture(root, ticks=1000000, start=200)
            (root/'uptime').write_text('10001 0')
            raw = balance_metrics(None, w.BALANCE_LIMITS, root/'slots', LinuxBalance(root, ticks=100), before=before)
            self.assertAlmostEqual(raw['top'][0]['cpu'], 1000000/(10001-2))
            before, _ = w.reduce('balance', before, raw)
            self.assertAlmostEqual(balance_metrics(None, w.BALANCE_LIMITS, root/'slots', LinuxBalance(root, ticks=100), before=before)['top'][0]['cpu'], 1000000/(10001-2))
            self.fixture(root, ticks=1, start=0)
            (root/'uptime').write_text('1 0')
            raw = balance_metrics(None, w.BALANCE_LIMITS, root/'slots', LinuxBalance(root, ticks=100), before=before)
            self.assertEqual(raw['top'][0]['cpu'], 1)
            self.fixture(root, ticks=1, start=200)
            (root/'uptime').write_text('10002 0')
            raw = balance_metrics(None, w.BALANCE_LIMITS, root/'slots', LinuxBalance(root, ticks=100), before=before)
            self.assertEqual(raw['top'][0]['cpu'], 1/10000)

    def test_linux_cycle_persists_and_prunes_samples(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proc = self.fixture(root)
            base = root/'state'
            def collect(before):
                with patch('watcher_platform.BALANCE', LinuxBalance(root, ticks=100)), \
                     patch('watcher_platform.live_slots', return_value=(0, [])):
                    return w.collect('balance', before)
            w.cycle(base, 'balance', collect)
            self.fixture(root, ticks=100100)
            (root/'uptime').write_text('10001 0')
            w.cycle(base, 'balance', collect)
            state = json.loads((base/'balance.json').read_text())['before']
            self.assertEqual(state['cpu_sample']['ticks'], {'7:100': 100100})
            (proc/'stat').unlink()
            (proc/'cwd').unlink()
            proc.rmdir()
            (root/'uptime').write_text('10002 0')
            w.cycle(base, 'balance', collect)
            state = json.loads((base/'balance.json').read_text())['before']
            self.assertEqual(state['cpu_sample']['ticks'], {})

    def test_generic_nested_git_worktree_root_and_flat_reserved_name(self):
        from watcher_platform import worktree_totals
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)/'worktrees'
            for root in (base/'project-a/task-123', base/'webapp'):
                (root/'src/deep').mkdir(parents=True)
                (root/'.git').write_text('gitdir: /fixture/common/worktrees/task\n')
                totals = worktree_totals([dict(cwd=str(root/'src/deep'), cpu=80, rss=1024)])
                self.assertEqual(totals[0]['name'], root.name)

    def test_same_named_worktrees_remain_distinct(self):
        from watcher_platform import worktree_totals
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)/'worktrees'
            roots = [base/'project-a/task', base/'project-b/task']
            for root in roots:
                root.mkdir(parents=True)
                (root/'.git').write_text('gitdir: /fixture/common/worktrees/task\n')
            totals = worktree_totals([dict(cwd=str(root), cpu=80, rss=1024) for root in roots])
            self.assertEqual(len(totals), 2)
            self.assertEqual({row['name'] for row in totals}, {str(root) for root in roots})
            self.assertEqual([row['cpu'] for row in totals], [80, 80])

    def test_unreadable_own_root_metadata_isolated_from_other_guards(self):
        class Adapter:
            def metrics(self, command):
                return dict(free=50, load5=21)
            def processes(self, command):
                return [dict(pid='7', cwd=str(root/'sub'), cpu=80, rss=1024)], []
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'worktrees/project/task'
            (root/'sub').mkdir(parents=True)
            (root/'.git').write_text('gitdir: /fixture/common/worktrees/task\n')
            original = Path.stat
            def stat(path, *args, **kwargs):
                if path == root/'.git':
                    raise PermissionError()
                return original(path, *args, **kwargs)
            with patch.object(Path, 'stat', stat):
                raw = balance_metrics(None, w.BALANCE_LIMITS, Path(tmp), Adapter())
        self.assertEqual(raw['unreadable'], ['pid:7'])
        self.assertIsNone(raw['heavy'])
        before, events = w.reduce('balance', None, raw)
        self.assertTrue(any('LOAD5(21.00)' in e for e in events))
        self.assertEqual(stuck(before, raw, w.READ_FAILURES - 1)[1], ['BALANCE READFAIL heavy pid:7'])

    def test_first_sample_lifetime_cpu_is_numeric_and_silent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture(root)
            raw = balance_metrics(None, w.BALANCE_LIMITS, root/'slots', LinuxBalance(root, ticks=100))
            self.assertAlmostEqual(raw['top'][0]['cpu'], 100000 / 100 / (10000-1) * 100)
            self.assertEqual(raw['heavy'], 0)
            self.assertEqual(w.reduce('balance', None, raw)[1], [])

    def test_new_pid_churn_does_not_generate_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture(root, ticks=0)
            base = root/'state'
            for index in range(6):
                (root/'uptime').write_text(str(10000+index)+' 0')
                short = root/'8'
                if index in (2, 4):
                    short.mkdir()
                    values = ['0']*22
                    values[0], values[11], values[19], values[21] = 'R', '10', str((9999+index)*100), '1'
                    (short/'stat').write_text('8 (short) '+' '.join(values))
                    (short/'cwd').symlink_to('/repo/worktrees/busy')
                elif short.exists():
                    (short/'stat').unlink()
                    (short/'cwd').unlink()
                    short.rmdir()
                line = w.cycle(base, 'balance', lambda before: balance_metrics(None, w.BALANCE_LIMITS, root/'slots', LinuxBalance(root, ticks=100), before=before))
                self.assertIsNone(line)

    def test_load_pressure_then_unreadable_slot_then_clean_recovers_once(self):
        clean = dict(load5=1, free=50, slots=0, heavy=0, top=[])
        before, _ = w.reduce('balance', None, dict(clean, load5=21))
        before, events = stuck(before, dict(clean, slots=None, unreadable=['slot:denied']))
        self.assertNotIn('BALANCE RECOVERED', events)
        self.assertTrue(before['pending_recovery'])
        before, events = w.reduce('balance', before, clean)
        self.assertEqual(events, ['BALANCE READFAIL cleared', 'BALANCE RECOVERED'])
        self.assertFalse(before['pending_recovery'])
        self.assertEqual(w.reduce('balance', before, clean)[1], [])

    def test_readfail_clear_without_pressure_does_not_recover(self):
        clean = dict(load5=1, free=50, slots=0, heavy=0, top=[])
        before, _ = stuck(None, dict(clean, slots=None))
        self.assertEqual(w.reduce('balance', before, clean)[1], ['BALANCE READFAIL cleared'])

    def test_exactly_four_known_worktrees_and_unreadable_pid_preserves_flag(self):
        adapter = MacBalance()
        rows = [dict(cwd='/repo/worktrees/job'+str(i), cpu=80, rss=1024) for i in range(4)]
        def command(*args):
            return 'free percentage: 50%' if args[0]=='memory_pressure' else '{ 1 1 1 }'
        with tempfile.TemporaryDirectory() as tmp, patch.object(adapter, 'processes', return_value=(rows, ['pid:5'])):
            raw = balance_metrics(command, w.BALANCE_LIMITS, Path(tmp), adapter)
        self.assertIsNone(raw['heavy'])
        before = dict(flags=dict(heavy=True), unreadable=[])
        state, events = w.reduce('balance', before, raw)
        self.assertIn('heavy', state['flags'])
        self.assertNotIn('BALANCE RECOVERED', events)

    def test_partial_decisions_use_the_configured_limits(self):
        adapter = MacBalance()
        rows = [dict(cwd='/repo/worktrees/job'+str(i), cpu=90, rss=1024) for i in range(4)]
        def command(*args):
            return 'free percentage: 50%' if args[0]=='memory_pressure' else '{ 1 1 1 }'
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            for i in range(3):
                (directory/str(i)).write_text(str(100+i))
            (directory/'bad').write_text('invalid')
            with patch.dict(w.BALANCE_LIMITS, slots=2, heavy=3, cpu=85), \
                 patch('watcher_platform.process_alive', return_value=True), \
                 patch.object(adapter, 'processes', return_value=(rows, ['pid:bad'])):
                raw = balance_metrics(command, limits=w.BALANCE_LIMITS, slots=directory, adapter=adapter)
                self.assertEqual((raw['slots'], raw['heavy']), (3, 4))
                self.assertEqual(set(w.conditions('balance', raw)), {'slots', 'heavy'})
                with patch.dict(w.BALANCE_LIMITS, cpu=95):
                    self.assertIsNone(balance_metrics(command, limits=w.BALANCE_LIMITS, slots=directory, adapter=adapter)['heavy'])

    def test_missing_slot_directory_is_zero(self):
        from watcher_platform import live_slots
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(live_slots(Path(tmp)/'absent'), (0, []))

    def test_host_and_balance_keep_distinct_recovery_policies(self):
        host = dict(free=50, swap=0, procs=1, files=1, load=1)
        steps, _ = w.reduce('host', None, dict(host, swap=2000))
        self.assertEqual(w.reduce('host', steps, dict(host, swap=2300))[1], [])
        self.assertEqual(w.reduce('host', steps, dict(host, swap=2400))[1], ['HOST SWAP(2400.00MB)'])
        before, _ = w.reduce('host', None, dict(host, swap=1500))
        self.assertEqual(w.reduce('host', before, dict(host, free=None))[1], ['HOST READFAIL free', 'HOST RECOVERED'])
        balance = dict(free=50, slots=0, heavy=0, load5=1, top=[])
        before, _ = stuck(None, dict(balance, slots=None))
        self.assertEqual(w.reduce('balance', before, balance)[1], ['BALANCE READFAIL cleared'])

    def test_first_sample_clock_is_after_process_birth(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture(root, ticks=20, start=150)
            (root/'uptime').write_text('1 0')
            adapter = LinuxBalance(root, ticks=100)
            original = adapter.process
            def process(path):
                row = original(path)
                (root/'uptime').write_text('2 0')
                return row
            with patch.object(adapter, 'process', side_effect=process):
                raw = balance_metrics(None, w.BALANCE_LIMITS, root/'slots', adapter)
            self.assertEqual(raw['top'][0]['cpu'], 40)
            self.assertEqual(raw['heavy'], 0)
