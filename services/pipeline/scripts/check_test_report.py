"""Fail CI when a pytest JUnit report is missing, empty, failed, or skipped."""

import argparse
import sys
from pathlib import Path
from xml.etree import ElementTree


class TestReportError(ValueError):
    """A report failure without printing test output or captured credentials."""


def check_test_report(path: Path) -> int:
    try:
        root = ElementTree.parse(path).getroot()
        suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
        counts = [
            {key: int(suite.attrib[key]) for key in ("tests", "skipped", "failures", "errors")}
            for suite in suites
        ]
    except (OSError, ElementTree.ParseError, KeyError, ValueError):
        raise TestReportError("invalid_or_missing_test_report") from None
    if not counts or any(value < 0 for count in counts for value in count.values()):
        raise TestReportError("invalid_or_missing_test_report")
    tests = sum(count["tests"] for count in counts)
    if tests == 0 or not list(root.iter("testcase")):
        raise TestReportError("empty_test_report")
    if any(count["skipped"] for count in counts) or list(root.iter("skipped")):
        raise TestReportError("skipped_tests")
    if (
        any(count["failures"] or count["errors"] for count in counts)
        or list(root.iter("failure"))
        or list(root.iter("error"))
    ):
        raise TestReportError("failed_tests")
    return tests


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", nargs="+", type=Path)
    args = parser.parse_args()
    for path in args.reports:
        try:
            count = check_test_report(path)
        except TestReportError as error:
            print(f"{path.name}: {error}", file=sys.stderr)
            return 1
        print(f"{path.name}: {count} tests, 0 skipped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
