"""Deadlines restore the alarm state whichever check point their alarm fires at."""

import contextlib
import dis
import functools
import gc
import signal
import sys
import time
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
            # A raising handler unsets the trace. On 3.12, code reports opcodes only
            # after a traced run and a later settrace, so each run follows a warm-up.
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

    def runtime_clock(self):
        clock = [0.0]
        real = runtime.time
        self.enterContext(
            patch.object(
                runtime,
                "time",
                SimpleNamespace(monotonic=lambda: real.monotonic() + clock[0]),
            )
        )
        return clock

    def deliver(self):
        """Runs the installed handler as a fired one-shot alarm."""
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.getsignal(signal.SIGALRM)(signal.SIGALRM, None)

    def count_deliveries(self, body, errors):
        """Counts the errors body raises, then any a pending timer delivers."""
        count = 0
        for step in (body, lambda: time.sleep(0.05)):
            try:
                step()
            except errors:
                count += 1
        return count

    def test_alarm_inside_restore_is_delivered_once(self):
        for outer, enclosing in (("soft", 0), ("soft", 500), ("poll", 0)):
            with self.subTest(outer=outer, enclosing=enclosing):
                clock = self.runtime_clock()
                poll = self.poll_clock()
                signal.signal(signal.SIGALRM, quiet)
                signal.setitimer(signal.ITIMER_REAL, enclosing)
                test = self

                class FiresOnce(list):
                    armed = True

                    def __setitem__(self, key, value):
                        super().__setitem__(key, value)
                        if self.armed:
                            self.armed = False
                            clock[0] = poll[0] = 100.0
                            test.deliver()

                seen = []

                def inner():
                    with runtime.deadline(1, "inner", soft=True):
                        pass

                manager = (
                    runtime.deadline(5, "outer", soft=True)
                    if outer == "soft"
                    else g.call_deadline(5, expired_error=Expired)
                )
                with patch.object(runtime, "_SOFT_DEADLINES", FiresOnce()):
                    try:
                        with manager:
                            held = signal.getsignal(signal.SIGALRM)
                            count = self.count_deliveries(
                                inner, (Expired, TimeoutError)
                            )
                            live = signal.getsignal(signal.SIGALRM)
                            timer = signal.getitimer(signal.ITIMER_REAL)[0]
                            seen.append((count, live, timer > 0, held))
                    except (Expired, TimeoutError):
                        pass
                signal.setitimer(signal.ITIMER_REAL, 0)
                [(count, live, armed, held)] = seen
                self.assertEqual(count, 1)
                self.assertIs(live, quiet if enclosing else held)
                self.assertEqual(armed, bool(enclosing))

    def test_alarm_in_a_kept_body_leaves_the_outer_handler_unarmed(self):
        seen = []
        with self.assertRaises(TimeoutError):
            with runtime.deadline(5, "outer", soft=True):
                held = signal.getsignal(signal.SIGALRM)

                def inner():
                    with runtime.deadline(50, "inner", soft=True):
                        self.deliver()

                count = self.count_deliveries(inner, TimeoutError)
                live = signal.getsignal(signal.SIGALRM)
                seen.append((count, live is held, signal.getitimer(0)))
        self.assertEqual(seen, [(1, True, (0.0, 0.0))])

    def fire_in_call_deadline_exit(self):
        exit_code = g.call_deadline.__exit__.__code__

        def fire(frame, _event, _arg):
            if frame.f_code is exit_code:
                sys.settrace(None)
                signal.setitimer(signal.ITIMER_REAL, 0)
                signal.getsignal(signal.SIGALRM)(signal.SIGALRM, frame)

        self.addCleanup(sys.settrace, None)
        sys.settrace(fire)

    def test_alarm_in_an_inherited_exit_is_delivered_once(self):
        clock = self.runtime_clock()
        poll = self.poll_clock()
        seen = []

        def inner():
            with g.call_deadline(10):
                clock[0] = poll[0] = 100.0
                self.fire_in_call_deadline_exit()

        with self.assertRaises(TimeoutError):
            with runtime.deadline(5, "outer", soft=True):
                seen.append(self.count_deliveries(inner, (g._Expired, TimeoutError)))
        self.assertEqual(seen, [1])

    def test_caught_inner_exit_error_keeps_the_outer_call_deadline(self):
        poll = self.poll_clock()
        seen = []
        with self.assertRaises(Expired):
            with g.call_deadline(5, expired_error=Expired):
                held = signal.getsignal(signal.SIGALRM)
                try:
                    with g.call_deadline(10):
                        poll[0] = 100.0
                        self.fire_in_call_deadline_exit()
                except Expired:
                    seen.append(signal.getsignal(signal.SIGALRM) is held)
        self.assertEqual(seen, [True])

    def test_caught_inner_exit_error_keeps_the_outer_timer_live(self):
        wall, real = [0.0], g.time
        self.enterContext(
            patch.object(
                g,
                "time",
                SimpleNamespace(
                    time=lambda: real.time() + wall[0], monotonic=real.monotonic
                ),
            )
        )
        seen = []
        with g.call_deadline(5, expired_error=Expired):
            held = signal.getsignal(signal.SIGALRM)
            wall[0] = -100.0
            try:
                with g.call_deadline(10, expired_error=Expired):
                    # Due by the inner wall clock, not by either outer clock.
                    wall[0] = -50.0
                    self.fire_in_call_deadline_exit()
            except Expired:
                live = signal.getitimer(signal.ITIMER_REAL)[0] > 0
                seen.append((signal.getsignal(signal.SIGALRM) is held, live))
            wall[0] = 0.0
        self.assertEqual(seen, [(True, True)])

    def test_inherited_alarm_in_the_body_keeps_the_inner_handler(self):
        poll = self.poll_clock()
        signal.setitimer(signal.ITIMER_REAL, 5)
        seen = []
        with g.call_deadline(10, expired_error=Expired):
            held = signal.getsignal(signal.SIGALRM)
            try:
                poll[0] = 100.0
                held(signal.SIGALRM, None)
            except Expired:
                seen.append(signal.getsignal(signal.SIGALRM) is held)
            poll[0] = 0.0
        self.assertEqual(seen, [True])

    def test_alarm_in_an_exit_keeps_a_non_callable_timer(self):
        poll = self.poll_clock()
        signal.signal(signal.SIGALRM, signal.SIG_IGN)
        signal.setitimer(signal.ITIMER_REAL, 5)
        with self.assertRaises(g._Expired):
            with g.call_deadline(10):
                poll[0] = 4.0 + 1.5
                self.fire_in_call_deadline_exit()
        self.assertGreater(signal.getitimer(signal.ITIMER_REAL)[0], 0)

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
