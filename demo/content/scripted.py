"""Scripted content for the demo agents (no AI involved). Edit the text freely.

Everything here is what a real lane agent would write. The stub agents only drop it
into the right files, run real git and real tests, and write synthetic telemetry.
"""

TICKET = "DEMO-1"
TITLE = "Show the tracking number on the order page"

TICKET_CONTEXT = """\
# DEMO-1: Show the tracking number on the order page

Kind: story. PRD refs: PRD-7#R2.

## Description
Customers ask support where their parcel is. Show the carrier tracking number on the order page.

## Jira criteria
| Id  | Criterion |
|-----|-----------|
| J-1 | The order API returns a tracking number once an order has shipped. |
| J-2 | The order page shows the tracking number for shipped orders. |
| J-3 | The order page shows "not yet available" while there is no tracking number. |

## Notes
Repos involved are not named by the requester. The Planner finds them (shop-api, shop-web).
"""

SPEC = """\
# Spec: tracking number on the order page

## Acceptance criteria
| Id   | Criterion |
|------|-----------|
| AC-1 | `GET order` returns `tracking_number` (string) for a shipped order. |
| AC-2 | `GET order` returns `tracking_number: null` for an order that has not shipped. |
| AC-3 | The order page prints `Tracking: <number>` for a shipped order. |
| AC-4 | The order page prints `Tracking: not yet available` when the number is null. |
| AC-5 | The order page also prints `Tracking: not yet available` when the field is missing (old API during a rolling deploy). |

## Traceability
| Source id | Spec AC | Result  | Reason |
|-----------|---------|---------|--------|
| J-1       | AC-1, AC-2 | kept, split | one criterion became two testable ones |
| J-2       | AC-3    | kept    | |
| J-3       | AC-4    | kept    | |
| (new)     | AC-5    | added   | the API and the web app are deployed one after the other, so the page must cope with the old response |

## Out of scope
Carrier links, tracking history, e-mail notifications.
"""

DESIGN = """\
# Design

- shop-api: add `tracking_number` to every order dict. Value is a string for shipped orders, `None` otherwise.
  This is an additive change to the response, so older clients keep working.
- shop-web: `render_order` appends one line, `Tracking: <number>` or `Tracking: not yet available`.
  Use `order.get("tracking_number")` so a missing field behaves like null (AC-5).
- Deploy shop-api first. If shop-web went first it would show "not yet available" for shipped orders until the API is live.
"""

TASKS = """\
# Tasks

| Task | Repo | Covers |
|------|------|--------|
| T-1 Add `tracking_number` to the order response | shop-api | AC-1, AC-2 |
| T-2 Render the tracking line on the order page | shop-web | AC-3, AC-4, AC-5 |

## Release
deploy_order: ordered
sequence: shop-api, shop-web
reason: shop-web reads a field that only exists after shop-api is deployed (AC-5 softens this, but the order still matters for users).
"""

TEST_PLAN = """\
# Test plan

## Coverage matrix
| AC   | Level | Repo | Test |
|------|-------|------|------|
| AC-1 | unit  | shop-api | tests/shop-api/test_acceptance.py::test_ac1_shipped_has_tracking_number |
| AC-2 | unit  | shop-api | tests/shop-api/test_acceptance.py::test_ac2_unshipped_has_null_tracking_number |
| AC-3 | unit  | shop-web | tests/shop-web/test_acceptance.py::test_ac3_shipped_shows_number |
| AC-4 | unit  | shop-web | tests/shop-web/test_acceptance.py::test_ac4_null_shows_not_yet_available |
| AC-5 | unit  | shop-web | tests/shop-web/test_acceptance.py::test_ac5_missing_field_shows_not_yet_available |

## Integration scenarios (placeholder: listed, run by hand)
- Given the new API is live and the old web app is running, when a customer opens a shipped order, then the page still renders.
- Given both are live, when a customer opens a shipped order, then the tracking number is visible.
"""

QE_TESTS = {
    "shop-api": '''\
import unittest

import orders


class AcceptanceTest(unittest.TestCase):
    def test_ac1_shipped_has_tracking_number(self):
        self.assertTrue(orders.get_order("A100")["tracking_number"])

    def test_ac2_unshipped_has_null_tracking_number(self):
        order = orders.get_order("A101")
        self.assertIn("tracking_number", order)
        self.assertIsNone(order["tracking_number"])


if __name__ == "__main__":
    unittest.main()
''',
    "shop-web": '''\
import unittest

import render


class AcceptanceTest(unittest.TestCase):
    def test_ac3_shipped_shows_number(self):
        order = {"id": "A1", "status": "shipped", "items": [], "tracking_number": "TRK-1"}
        self.assertIn("Tracking: TRK-1", render.render_order(order))

    def test_ac4_null_shows_not_yet_available(self):
        order = {"id": "A2", "status": "processing", "items": [], "tracking_number": None}
        self.assertIn("Tracking: not yet available", render.render_order(order))

    def test_ac5_missing_field_shows_not_yet_available(self):
        order = {"id": "A3", "status": "shipped", "items": []}
        self.assertIn("Tracking: not yet available", render.render_order(order))


if __name__ == "__main__":
    unittest.main()
''',
}

# Dev output per repo and attempt: files to write (path -> text), commit message, summary.
DEV = {
    ("shop-api", 1): {
        "files": {
            "orders.py": '''\
"""Tiny fake order service used by the workflow demo."""

ORDERS = {
    "A100": {"id": "A100", "status": "shipped", "items": ["mug"], "tracking_number": "TRK-778899"},
    "A101": {"id": "A101", "status": "processing", "items": ["poster"], "tracking_number": None},
}


def get_order(order_id):
    """Return a copy of the order, or raise KeyError."""
    return dict(ORDERS[order_id])
''',
            "tests/test_tracking.py": '''\
import unittest

import orders


class TrackingTest(unittest.TestCase):
    def test_shipped_order_has_number(self):
        self.assertEqual(orders.get_order("A100")["tracking_number"], "TRK-778899")

    def test_unshipped_order_has_null(self):
        self.assertIsNone(orders.get_order("A101")["tracking_number"])


if __name__ == "__main__":
    unittest.main()
''',
        },
        "commit": "Return tracking_number in the order response",
        "summary": "Added `tracking_number` to every order (string when shipped, null otherwise). Added two unit tests.",
        "notes": "Touches the API response shape (additive). Signal: touches_api_contract.",
    },
    ("shop-web", 1): {
        "files": {
            "render.py": '''\
"""Tiny fake order page used by the workflow demo."""


def render_order(order):
    lines = [f"Order {order['id']} ({order['status']})"]
    for item in order.get("items", []):
        lines.append(f"  - {item}")
    lines.append("Tracking: " + str(order.get("tracking_number")))
    return "\\n".join(lines)
''',
            "tests/test_tracking.py": '''\
import unittest

import render


class TrackingTest(unittest.TestCase):
    def test_shipped_order_shows_number(self):
        order = {"id": "A1", "status": "shipped", "items": [], "tracking_number": "TRK-1"}
        self.assertIn("Tracking: TRK-1", render.render_order(order))


if __name__ == "__main__":
    unittest.main()
''',
        },
        "commit": "Show the tracking number on the order page",
        "summary": "Added a `Tracking:` line to the order page. One unit test (shipped order).",
        "notes": "UI only. No risk signal.",
    },
    ("shop-web", 2): {
        "files": {
            "render.py": '''\
"""Tiny fake order page used by the workflow demo."""


def render_order(order):
    lines = [f"Order {order['id']} ({order['status']})"]
    for item in order.get("items", []):
        lines.append(f"  - {item}")
    number = order.get("tracking_number")
    lines.append(f"Tracking: {number}" if number else "Tracking: not yet available")
    return "\\n".join(lines)
''',
            "tests/test_tracking.py": '''\
import unittest

import render


class TrackingTest(unittest.TestCase):
    def test_shipped_order_shows_number(self):
        order = {"id": "A1", "status": "shipped", "items": [], "tracking_number": "TRK-1"}
        self.assertIn("Tracking: TRK-1", render.render_order(order))

    def test_null_shows_not_yet_available(self):
        order = {"id": "A2", "status": "processing", "items": [], "tracking_number": None}
        self.assertIn("Tracking: not yet available", render.render_order(order))

    def test_missing_field_shows_not_yet_available(self):
        self.assertIn("Tracking: not yet available", render.render_order({"id": "A3", "status": "shipped"}))


if __name__ == "__main__":
    unittest.main()
''',
        },
        "commit": "Show 'not yet available' when there is no tracking number",
        "summary": "Fixed the null case (was printing 'Tracking: None'). Added tests for null and missing field.",
        "notes": "Rework after verify_failed (AC-4, AC-5). No risk signal.",
    },
}

REVIEW_SIGNALS = ["multi_repo_ordered_deploy", "touches_api_contract"]

REVIEW_BODY = """\
Verdict: pass

## Review
- Code matches spec.md and design.md. No scope creep: two small, additive changes.
- shop-web reads the new field defensively (`order.get`), so AC-5 holds during the rolling deploy.
- Rework history: shop-web failed verify once (AC-4, AC-5) and was fixed in attempt 2.

## Risk signals matched (config/risk-rules.yaml)
- `multi_repo_ordered_deploy`: tasks.md says deploy shop-api, then shop-web.
- `touches_api_contract`: the order response has a new field.

A human must approve this review before deploy.
"""

# Synthetic token usage per (lane, repo, attempt): (calls, input, output, tool calls)
TOKENS = {
    ("planner", "", 1): (6, 41000, 6200, 9),
    ("qe", "", 1): (5, 33000, 5100, 7),
    ("dev", "shop-api", 1): (4, 22000, 3300, 8),
    ("dev", "shop-web", 1): (4, 24000, 3500, 8),
    ("dev", "shop-web", 2): (3, 19000, 2600, 6),
    ("reviewer", "", 1): (4, 36000, 2200, 10),
    ("reviewer", "", 2): (5, 44000, 3900, 11),
    ("inspect-adapt", "", 1): (5, 38000, 4400, 8),
}

INSPECTION = """\
# Inspection report: {ticket}

## What happened
- Kind: story. Lanes ran: planner, qe, dev (shop-api, shop-web), reviewer, inspect-adapt.
- Rework loops: {loops} (shop-web failed verify once).
- Sessions with telemetry: {sessions}. Tokens in/out (synthetic demo numbers): {tin} / {tout}.

## Findings
1. **shop-web shipped a null bug.** Dev wrote one local test (shipped order only). The null and missing-field cases were found only at verify, which cost one extra Dev session and one extra Reviewer session.
   Evidence: verify-report.md (attempt 1), metrics/dev-shop-web-a1.json, metrics/dev-shop-web-a2.json.
2. Spec and test plan were clean: no `spec_gap`, no question raised.

## Adapt
One candidate for the Dev lane (see ledger L-2026-11-001). It is advisory only and removable.
"""

LEDGER = {
    "id": "L-2026-11-001",
    "lane": "dev",
    "observation": "Dev tested only the happy path in shop-web; null and missing-field cases failed at verify and cost a rework loop",
    "change": "Add a checklist item: write at least one local test per AC, including null and missing-value cases",
}

OVERLAY = """\
# L-2026-11-001 (candidate for the Dev lane)

Why: on DEMO-1 the shop-web change passed its own tests but failed verify on null and missing values (one rework loop).

Checklist item to add:
- Before you finish, write at least one local test for every AC in your tasks, including the null and missing-value cases. List the AC ids in `notes.md`.

Scope: advisory only. Expires after one sprint unless the retro promotes it. Auto-reverts if the next 3 tickets show no gain.
"""

# A second ticket, used to show the overlay being picked up by the launcher.
NEXT_TICKET = "DEMO-2"
