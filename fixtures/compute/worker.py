"""Compute fixture with stable, explicit CLI behavior."""

import re
import sys

MAX_COUNT = 3_000_000


def main(arguments):
    if len(arguments) != 1:
        print("expected one count", file=sys.stderr)
        return 2
    count = arguments[0]
    if count == "":
        print("count is empty", file=sys.stderr)
        return 2
    if not re.fullmatch("[0-9]+", count):
        print("count must use ASCII digits", file=sys.stderr)
        return 2
    count = count.lstrip("0") or "0"
    if len(count) > len(str(MAX_COUNT)) or int(count) > MAX_COUNT:
        print("count exceeds 3000000", file=sys.stderr)
        return 2
    value = 1
    for index in range(int(count)):
        value = (value * 1_664_525 + 1_013_904_223 + index) & 0xFFFF_FFFF
    print(value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
