"""Fixture's Python workflow entry point, shared by baseline and candidate."""

import os
import sys

from worker import main as python_worker


def main(arguments):
    native_worker = None
    if len(arguments) >= 2 and arguments[0] == "--native":
        native_worker = arguments[1]
        arguments = arguments[2:]
    if not arguments or arguments[0] != "compute":
        print("usage: workflow.py compute COUNT", file=sys.stderr)
        return 2
    count = arguments[1:]
    if native_worker is not None:
        os.execv(native_worker, [native_worker, *count])
    return python_worker(count)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
