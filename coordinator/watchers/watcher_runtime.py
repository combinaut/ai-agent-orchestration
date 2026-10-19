"""Bounded execution for Unix watcher services and foreground monitors."""
import contextlib
import faulthandler
import os
import signal
import subprocess
import time

COMMAND_TIMEOUT = 45
ITERATION_TIMEOUT = 600
LOCK_TIMEOUT = 5
TRACEBACK_GRACE = 30
_HARD_DEADLINES = []
_SOFT_DEADLINES = []
_ACTIVE_PROCESS = None


def kill_command(process):
    # Only the private process group created by run(), never a host job.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def run(argv, timeout=None):
    """A command owns its descendants; cleanup never waits for inherited pipe EOF."""
    global _ACTIVE_PROCESS
    for pending in _SOFT_DEADLINES:
        if pending['expired']:
            raise TimeoutError(f"pass deadline exceeded: {pending['label']}")
    timeout = COMMAND_TIMEOUT if timeout is None else timeout
    process = None
    pending_alarm = False
    def defer_alarm(_signal, _frame):
        nonlocal pending_alarm
        pending_alarm = True
    # Defer Python delivery rather than block SIGALRM's OS mask, which a
    # spawned command would inherit. The C-level backstop remains armed.
    previous_handler = signal.signal(signal.SIGALRM, defer_alarm)
    try:
        try:
            # Git diagnostics are matched in English.
            env = dict(os.environ, LC_ALL='C') if os.path.basename(argv[0]) == 'git' else None
            process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       start_new_session=True, env=env)
            _ACTIVE_PROCESS = process
        finally:
            signal.signal(signal.SIGALRM, previous_handler)
            if pending_alarm:
                # Both soft exceptions and hard exits now see the owned group.
                signal.raise_signal(signal.SIGALRM)
        stdout, stderr = process.communicate(timeout=timeout)
        if process.returncode:
            kill_command(process)
        # Preserve byte-level line endings for legacy diffs, decode stdout
        # strictly, and keep malformed diagnostic bytes readable.
        return subprocess.CompletedProcess(argv, process.returncode,
                                           stdout.decode(), stderr.decode(errors='replace'))
    except BaseException:
        if process is not None:
            kill_command(process)
            # Reap only our child, never wait for a descendant to release pipe EOF.
            process.wait(timeout=1)
        raise
    finally:
        if process is not None:
            process.stdout.close()
            process.stderr.close()
        _ACTIVE_PROCESS = None


def arm_traceback_backstop():
    if _HARD_DEADLINES:
        remaining = min(_HARD_DEADLINES) - time.monotonic()
        faulthandler.dump_traceback_later(max(0.001, remaining + TRACEBACK_GRACE),
                                        file=2, exit=True)
    else:
        faulthandler.cancel_dump_traceback_later()


@contextlib.contextmanager
def deadline(seconds, label, *, soft=False):
    """Hard exit bypasses broad read-error handlers and releases locks on death.

    Only used by the main thread. Nested guards retain the earlier deadline.
    SIGALRM also interrupts an in-process read/lock, with no child to time out.
    """
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    started = time.monotonic()
    pending = {'expired': False, 'label': label}
    if soft:
        _SOFT_DEADLINES.append(pending)
    else:
        _HARD_DEADLINES.append(started + seconds)
        arm_traceback_backstop()
    def expired(_signal, _frame):
        if soft:
            pending['expired'] = True
            # Keep the enclosing hard alarm live if a collector catches this.
            if previous_timer[0]:
                signal.signal(signal.SIGALRM, previous_handler)
                signal.setitimer(signal.ITIMER_REAL, max(0.001, previous_timer[0] - (time.monotonic() - started)))
            raise TimeoutError(f'pass deadline exceeded: {label} ({seconds}s)')
        blocking = None
        try:
            # Restore the shared open-file-description flag before exiting.
            blocking = os.get_blocking(2)
            os.set_blocking(2, False)
            os.write(2, f'watcher deadline exceeded: {label} ({seconds}s)\n'.encode())
            if _ACTIVE_PROCESS is not None:
                os.write(2, f'active command: {_ACTIVE_PROCESS.args!r}\n'.encode())
            faulthandler.dump_traceback(file=2, all_threads=True)
        except OSError:
            pass
        finally:
            try:
                if _ACTIVE_PROCESS is not None:
                    kill_command(_ACTIVE_PROCESS)
                if blocking is not None:
                    os.set_blocking(2, blocking)
            finally:
                os._exit(70)
    keep_previous = previous_timer[0] > 0 and previous_timer[0] <= seconds
    signal.signal(signal.SIGALRM, previous_handler if keep_previous else expired)
    signal.setitimer(signal.ITIMER_REAL, previous_timer[0] if keep_previous else seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0]:
            signal.setitimer(signal.ITIMER_REAL, max(0.001, previous_timer[0] - (time.monotonic() - started)),
                             previous_timer[1])
        if soft:
            _SOFT_DEADLINES.remove(pending)
        else:
            _HARD_DEADLINES.remove(started + seconds)
            arm_traceback_backstop()
        # Source-level OSError handlers can swallow the first signal. Keep an
        # exhausted pass from being committed as a success after they return.
        if pending['expired']:
            raise TimeoutError(f'pass deadline exceeded: {label} ({seconds}s)')


def sleep(interval, clock=None, wait=None):
    """No wall-clock arithmetic; each wait is bounded even after a clock reset."""
    clock, wait = clock or time.monotonic, wait or time.sleep
    with deadline(interval + 30, 'loop sleep'):
        end = clock() + interval
        while True:
            remaining = min(interval, max(0, end - clock()))
            if not remaining:
                return
            wait(min(5, remaining))


def iterations(interval, repeat=True, label='iteration', budget=ITERATION_TIMEOUT):
    while True:
        with deadline(budget, label) if budget is not None else contextlib.nullcontext():
            yield
        if not repeat:
            return
        sleep(interval)
