"""Console output reporting for E2E test results."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ScenarioResult:
    name: str
    failures: list[str] = field(default_factory=list)
    assertions: int = 0
    passed_assertions: int = 0

    @property
    def failed(self) -> bool:
        return len(self.failures) > 0

    def assert_true(self, condition: bool, description: str):
        self.assertions += 1
        if condition:
            self.passed_assertions += 1
            print(f"    \u2705 {description}")
        else:
            self.failures.append(description)
            print(f"    \u274c {description}")

    def assert_not_none(self, value: object, description: str):
        self.assert_true(value is not None, description)

    def assert_contains(self, haystack: str, needle: str, description: str):
        self.assert_true(needle in haystack, description)

    def fail(self, reason: str):
        self.failures.append(reason)
        print(f"    \u274c {reason}")


class Reporter:
    def __init__(self):
        self.results: list[ScenarioResult] = []
        self.current_result: ScenarioResult | None = None

    def start_scenario(self, name: str):
        self.current_result = ScenarioResult(name=name)
        self.results.append(self.current_result)

    def pass_scenario(self):
        pass  # No failures = passed

    def fail_scenario(self, reason: str):
        if self.current_result:
            self.current_result.fail(reason)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if not r.failed)

    @property
    def failed_count(self) -> int:
        return sum(1 for r in self.results if r.failed)

    def print_summary(self):
        print()
        print("=" * 60)
        print("  E2E Test Results")
        print("=" * 60)
        print()

        for result in self.results:
            status = "\u2705 PASS" if not result.failed else "\u274c FAIL"
            print(f"  {status}  {result.name}")
            if result.failed:
                for f in result.failures:
                    print(f"         - {f}")

        print()
        print("-" * 60)
        total = self.total
        passed = self.passed
        failed = self.failed_count
        color = "\u2705" if failed == 0 else "\u274c"
        print(f"  {color} {passed}/{total} scenarios passed, {failed} failed")
        print("=" * 60)
