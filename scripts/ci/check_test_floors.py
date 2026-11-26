"""Enforce monotonic test-count floors for pytest and XCTest.

agentic-uplift's own floors only count Node tests, so this fills the gap for
our suites. Reads a JUnit/xUnit XML report and test-floors.json:

    python3 scripts/ci/check_test_floors.py pytest build/pytest.xml
    python3 scripts/ci/check_test_floors.py swift  build/swift-xunit.xml

Fails if passed < minPassed, skipped > maxSkipped, or anything failed.
Stdlib only.
"""

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def count(report: Path):
    tree = ET.parse(report)
    passed = failed = skipped = 0
    for case in tree.iter("testcase"):
        tags = {child.tag for child in case}
        if tags & {"failure", "error"}:
            failed += 1
        elif "skipped" in tags:
            skipped += 1
        else:
            passed += 1
    return passed, failed, skipped


def main(argv) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    suite, report = argv[1], Path(argv[2])
    floors = json.loads((ROOT / "test-floors.json").read_text())["suites"]
    if suite not in floors:
        print(f"unknown suite {suite!r}; known: {', '.join(floors)}", file=sys.stderr)
        return 2
    floor = floors[suite]
    passed, failed, skipped = count(report)
    print(
        json.dumps(
            {
                "suite": suite,
                "passed": passed,
                "failed": failed,
                "skipped": skipped,
                "minPassed": floor["minPassed"],
                "maxSkipped": floor["maxSkipped"],
            }
        )
    )
    problems = []
    if failed:
        problems.append(f"{failed} test(s) failed")
    if passed < floor["minPassed"]:
        problems.append(
            f"only {passed} passed; floor is {floor['minPassed']} (tests may not be removed or disabled)"
        )
    if skipped > floor["maxSkipped"]:
        problems.append(f"{skipped} skipped; ceiling is {floor['maxSkipped']}")
    if passed > floor["minPassed"]:
        print(f"note: {passed} > floor {floor['minPassed']}; raise the floor in test-floors.json")
    for p in problems:
        print(f"FAIL {suite}: {p}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
