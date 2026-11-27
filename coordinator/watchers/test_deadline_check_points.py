"""Deadlines restore the alarm state whichever check point their alarm fires at."""

import contextlib
import dis
import functools
import gc
import signal
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test_support import IsolatedTestCase

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import github_poll as g
import watcher_runtime as runtime


class Expired(BaseException):
    pass


def quiet(_signum, _frame):
    pass


@functools.cache
def check_points(code):
    """Offsets where the interpreter runs pending signal handlers, after a call or at a back jump."""
    instructions = list(dis.get_instructions(code))
    return {
        current.offset
        for previous, current in zip(instructions, instructions[1:])
        if previous.opname.startswith("CALL")
    } | {i.offset for i in instructions if i.opname.startswith("JUMP_BACKWARD")}


class AlarmAt:
    """Runs the installed SIGALRM handler at the n-th signal check point of the modules."""

    def __init__(self, modules, clock):
        self.namespaces = [vars(module) for module in modules]
        self.called = set()
        self.clock = clock
        self.arm(0)

    def arm(self, n):
        self.n = n
        self.seen = 0
        self.fired = False
        self.where = None

    def __enter__(self):
        sys.settrace(self.call)
        return self

    def __exit__(self, *_exc):
        sys.settrace(None)

    def call(self, frame, _event, _arg):
        self.called.add(frame.f_code.co_filename)
        if not any(frame.f_globals is namespace for namespace in self.namespaces):
            return None
        frame.f_trace_opcodes = True
        entered = [True]

        def opcode(frame, event, _arg):
            if event == "opcode":
                # A function entry or generator resume is a check point too.
                if entered[0] or frame.f_lasti in check_points(frame.f_code):
                    self.check_point(frame)
                entered[0] = False
            return opcode

        return opcode

    def check_point(self, frame):
        if self.fired:
            return
        self.seen += 1
        if self.seen == self.n:
            self.fired = True
            self.where = f"{frame.f_code.co_qualname} +{frame.f_lasti}"
            self.clock[0] = 1000.0
            handler = signal.getsignal(signal.SIGALRM)
            if callable(handler):
                handler(signal.SIGALRM, frame)


class DeadlineCheckPointTests(IsolatedTestCase):
    def setUp(self):
        self.previous_handler = signal.signal(signal.SIGALRM, quiet)
        self.previous_timer = signal.setitimer(signal.ITIMER_REAL, 0)
        self.addCleanup(self.reset)

    def reset(self):
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, self.previous_handler)
        if self.previous_timer[0]:
            signal.setitimer(signal.ITIMER_REAL, *self.previous_timer)

    def sweep(self, modules, body, clock, expected=()):
        """Fires the alarm at each check point in turn; every run leaves the state clean."""
        clean = (quiet, (0.0, 0.0))

        def state():
            return signal.getsignal(signal.SIGALRM), signal.getitimer(
                signal.ITIMER_REAL
            )

        alarm = self.enterContext(AlarmAt(modules, clock))
        # With SIGALRM blocked, only AlarmAt runs the handler, so each run is deterministic.
        signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGALRM})
        self.addCleanup(signal.pthread_sigmask, signal.SIG_UNBLOCK, {signal.SIGALRM})
        n = 0
        places = set()
        held = []
        while True:
            n += 1
            clock[0] = 0.0
            # A raising handler unsets the trace. Code reports opcodes only after a
            # traced run and a later settrace, so each run follows a warm-up run.
            alarm.arm(0)
            sys.settrace(alarm.call)
            body()
            sys.settrace(alarm.call)
            alarm.arm(n)
            try:
                body()
            except (Expired, TimeoutError) as error:
                # The caller still holds the error, as prs.py's cycle error does.
                held.append(error)
            at_exit = state()
            leftovers = [list(pending) for pending in expected]
            held.clear()
            gc.collect()
            after_gc = state()
            # A blocked alarm the timer raised is discarded while ignored.
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, signal.SIG_IGN)
            signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGALRM})
            signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGALRM})
            signal.signal(signal.SIGALRM, quiet)
            where = f"alarm at {alarm.where}"
            self.assertEqual(at_exit, clean, f"{where}: state after the with")
            self.assertEqual(after_gc, clean, f"{where}: state after gc")
            self.assertEqual(leftovers, [[] for _ in expected], where)
            if not alarm.fired:
                self.assertGreater(n, 1, f"no check point traced in {alarm.called}")
                return places
            places.add(alarm.where.split(" ")[0])

    def poll_clock(self):
        clock = [0.0]
        real = g.time
        self.enterContext(
            patch.object(
                g,
                "time",
                SimpleNamespace(
                    time=lambda: real.time() + clock[0],
                    monotonic=lambda: real.monotonic() + clock[0],
                ),
            )
        )
        return clock

    def test_call_deadline_restores_at_every_check_point(self):
        clock = self.poll_clock()

        def body():
            with g.call_deadline(5, expired_error=Expired):
                pass

        self.assertLessEqual(
            {"call_deadline.__enter__", "call_deadline.__exit__"},
            self.sweep((g, contextlib), body, clock),
        )

    def test_nested_soft_deadlines_restore_at_every_check_point(self):
        pending = []

        def body():
            with runtime.deadline(50, "outer", soft=True):
                try:
                    with runtime.deadline(5, "inner", soft=True):
                        pass
                except TimeoutError:
                    pass
                pending.append([entry["label"] for entry in runtime._SOFT_DEADLINES])

        places = self.sweep(
            (runtime, contextlib), body, [0.0], expected=(runtime._SOFT_DEADLINES,)
        )
        self.assertLessEqual({"deadline.__enter__", "deadline.__exit__"}, places)
        # The outer deadline stays pending through every alarm in the inner exit.
        self.assertEqual({tuple(labels) for labels in pending}, {("outer",)})

    def test_hard_deadline_inside_a_soft_deadline_restores_at_every_check_point(self):
        # A failing run leaves the exiting traceback backstop armed.
        self.addCleanup(runtime.faulthandler.cancel_dump_traceback_later)
        self.addCleanup(runtime._HARD_DEADLINES.clear)

        raised = set()

        def body():
            with runtime.deadline(5, "outer", soft=True):
                try:
                    # The earlier outer alarm stays installed, so no alarm hard-exits.
                    with runtime.deadline(50, "inner"):
                        pass
                except BaseException as error:
                    # The outer exit replaces this error with its own TimeoutError.
                    raised.add(type(error))
                    raise

        places = self.sweep(
            (runtime, contextlib),
            body,
            [0.0],
            expected=(runtime._HARD_DEADLINES, runtime._SOFT_DEADLINES),
        )
        self.assertIn("arm_traceback_backstop", places)
        self.assertEqual(raised, {TimeoutError})

    def test_kept_alarm_in_exit_restores_without_rearming(self):
        signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGALRM})
        self.addCleanup(signal.pthread_sigmask, signal.SIG_UNBLOCK, {signal.SIGALRM})
        exit_code = runtime.deadline.__exit__.__code__

        def fire(frame, _event, _arg):
            if frame.f_code is exit_code:
                sys.settrace(None)
                # A fired one-shot alarm leaves no timer.
                signal.setitimer(signal.ITIMER_REAL, 0)
                signal.getsignal(signal.SIGALRM)(signal.SIGALRM, frame)

        self.addCleanup(sys.settrace, None)
        seen = []
        with self.assertRaises(TimeoutError):
            with runtime.deadline(5, "outer", soft=True):
                with self.assertRaises(TimeoutError):
                    with runtime.deadline(50, "inner", soft=True):
                        sys.settrace(fire)
                timer = signal.getitimer(signal.ITIMER_REAL)
                seen.append((timer, [p["label"] for p in runtime._SOFT_DEADLINES]))
        self.assertEqual(seen, [((0.0, 0.0), ["outer"])])

    def test_kept_alarm_carries_the_poll_deadline(self):
        with g.call_deadline(5):
            with runtime.deadline(50, "kept", soft=True):
                with g.call_deadline(10):
                    expires = signal.getsignal(signal.SIGALRM)._poll_deadline[0]
                    # The inherited limit is the outer 5s, not its clock-check tick.
                    self.assertGreater(expires - g.time.time(), 4)

    def test_kept_non_callable_alarm_stays_installed(self):
        signal.signal(signal.SIGALRM, signal.SIG_IGN)
        signal.setitimer(signal.ITIMER_REAL, 5)
        with runtime.deadline(50, "kept", soft=True):
            self.assertEqual(signal.getsignal(signal.SIGALRM), signal.SIG_IGN)

    def test_call_deadline_inside_a_soft_deadline_marks_it_expired(self):
        clock = self.poll_clock()
        escaped = []

        def body():
            try:
                with runtime.deadline(5, "outer", soft=True):
                    with g.call_deadline(10):
                        pass
            except g._Expired:
                escaped.append(True)

        self.sweep(
            (g, runtime, contextlib), body, clock, expected=(runtime._SOFT_DEADLINES,)
        )
        # The inherited soft deadline expires whenever the call deadline fires.
        self.assertEqual(escaped, [])
