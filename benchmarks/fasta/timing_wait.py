"""Bounded Linux pidfd notification, then exact-supervisor-PID wait4 reap."""
import math
import os
import select
import time


def wait_for_child(child, deadline, kill_group):
    if not callable(getattr(os, 'pidfd_open', None)) or not callable(getattr(select, 'poll', None)):
        raise RuntimeError('Linux pidfd/poll support is required; no polling fallback')
    descriptor = os.pidfd_open(child.pid, 0)
    if type(descriptor) is not int or descriptor < 0:
        raise RuntimeError('Invalid pidfd descriptor')
    def reap(options):
        while True:
            try:
                pid, status, usage = os.wait4(child.pid, options)
                if pid not in (0, child.pid):
                    raise RuntimeError('wait4 returned a different PID')
                return pid, status, usage
            except InterruptedError:
                continue
    def expired():
        pid, status, usage = reap(os.WNOHANG)
        if pid:
            return os.waitstatus_to_exitcode(status), usage.ru_maxrss, False
        kill_group(child.pid)
        pid, status, usage = reap(0)
        if pid != child.pid:
            raise RuntimeError('Killed supervisor was not reaped')
        return os.waitstatus_to_exitcode(status), usage.ru_maxrss, True
    try:
        poller = select.poll()
        poller.register(descriptor, select.POLLIN)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return expired()
            try:
                events = poller.poll(max(1, math.ceil(remaining * 1000)))
            except InterruptedError:
                continue
            if time.monotonic() >= deadline:
                return expired()
            if not events:
                continue
            if len(events) != 1 or events[0] != (descriptor, select.POLLIN):
                raise RuntimeError('Invalid pidfd readiness event')
            # Never block on a merely asserted readiness indication. This exact
            # WNOHANG wait4 both verifies termination and obtains status/rusage.
            pid, status, usage = reap(os.WNOHANG)
            if pid != child.pid:
                raise RuntimeError('Spurious pidfd readiness without exited supervisor')
            return os.waitstatus_to_exitcode(status), usage.ru_maxrss, False
    finally:
        os.close(descriptor)
