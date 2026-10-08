"""Tests for conduit_sdk.policy — structured policy (Phase 3)."""

from __future__ import annotations

from conduit_sdk.policy import (
    Action,
    ApprovalDecision,
    ApprovalRequest,
    PolicyDecision,
    compose,
    deny,
    max_cost_usd,
    max_runtime_minutes,
    read_only,
    require_approval_for,
    safe_local,
)


# ---------------------------------------------------------------------------
# Wire records (camelCase)
# ---------------------------------------------------------------------------


class TestPolicyRecords:
    def test_action_to_record_camelcase(self) -> None:
        a = Action(kind="tool", tool_name="write_file", command="x", path="p")
        rec = a.to_record()
        assert rec == {"kind": "tool", "command": "x", "path": "p", "toolName": "write_file"}

    def test_policydecision_to_record(self) -> None:
        d = PolicyDecision(effect="require_approval", risk="med", kind="command",
                           reason="r", approval_id="apr_1")
        assert d.to_record() == {"effect": "require_approval", "risk": "med",
                                 "reason": "r", "kind": "command", "approvalId": "apr_1"}

    def test_approval_request_and_decision_records(self) -> None:
        req = ApprovalRequest(approval_id="a1", kind="command", risk="high",
                              reason="ok", action_preview={"command": "rm"})
        assert req.to_record()["actionPreview"] == {"command": "rm"}
        dec = ApprovalDecision(decision="approve", by="kevin")
        assert dec.to_record() == {"decision": "approve", "by": "kevin"}


# ---------------------------------------------------------------------------
# Factory behavior
# ---------------------------------------------------------------------------


class TestReadOnly:
    def test_denies_mutations(self) -> None:
        p = read_only()
        assert p.evaluate(Action("command", command="ls")).effect == "deny"
        assert p.evaluate(Action("file_write", path="a.py")).effect == "deny"
        assert p.evaluate(Action("diff_apply", path="a.py")).effect == "deny"

    def test_allows_reads_and_tools(self) -> None:
        p = read_only()
        assert p.evaluate(Action("file_read", path="a.py")).effect == "allow"
        assert p.evaluate(Action("tool", tool_name="read_file")).effect == "allow"


class TestRequireApprovalFor:
    def test_command_prefix_match(self) -> None:
        p = require_approval_for(commands=["git push"])
        assert p.evaluate(Action("command", command="git push origin")).effect == "require_approval"
        assert p.evaluate(Action("command", command="git status")).effect == "allow"

    def test_command_star_matches_any(self) -> None:
        p = require_approval_for(commands=["*"])
        assert p.evaluate(Action("command", command="anything here")).effect == "require_approval"

    def test_file_glob(self) -> None:
        p = require_approval_for(files=[".env*", "src/**"])
        assert p.evaluate(Action("file_write", path=".env.production")).effect == "require_approval"
        assert p.evaluate(Action("file_write", path="src/a/b.py")).effect == "require_approval"
        assert p.evaluate(Action("file_write", path="README.md")).effect == "allow"


class TestCompose:
    def test_deny_beats_require_beats_allow(self) -> None:
        p = compose(
            require_approval_for(commands=["git"]),
            deny(reason="hard no"),
        )
        # deny wins even when another branch would require_approval
        assert p.evaluate(Action("command", command="git push")).effect == "deny"

    def test_require_beats_allow(self) -> None:
        p = compose(
            require_approval_for(commands=["rm"]),
            max_runtime_minutes(5),  # allows everything; require must win
        )
        assert p.evaluate(Action("command", command="rm x")).effect == "require_approval"

    def test_empty_compose_allows(self) -> None:
        assert compose().evaluate(Action("command", command="x")).effect == "allow"

    def test_budget_last_non_none_wins(self) -> None:
        p = compose(max_runtime_minutes(5), max_cost_usd(1.0))
        assert p.budget == {"max_cost_usd": 1.0}
        p2 = compose(max_cost_usd(1.0), max_runtime_minutes(5))
        assert p2.budget == {"max_runtime_s": 300}


class TestSafeLocal:
    def test_blocks_risky_commands_and_paths(self) -> None:
        p = safe_local()
        assert p.evaluate(Action("command", command="git push")).effect == "require_approval"
        assert p.evaluate(Action("command", command="npm install")).effect == "require_approval"
        assert p.evaluate(Action("file_write", path=".env")).effect == "require_approval"
        assert p.evaluate(Action("file_write", path=".github/workflows/ci.yml")).effect == "require_approval"
        # Benign reads are allowed.
        assert p.evaluate(Action("file_read", path="src/x.py")).effect == "allow"


class TestBudgetFactories:
    def test_max_runtime_minutes_budget(self) -> None:
        p = max_runtime_minutes(2)
        assert p.budget == {"max_runtime_s": 120}
        assert p.evaluate(Action("command", command="x")).effect == "allow"

    def test_max_cost_usd_budget(self) -> None:
        p = max_cost_usd(0.5)
        assert p.budget == {"max_cost_usd": 0.5}


# ---------------------------------------------------------------------------
# Unknown kind defaults to allow
# ---------------------------------------------------------------------------


def test_unknown_kind_defaults_allow() -> None:
    for factory in (read_only, safe_local):
        p = factory()
        assert p.evaluate(Action("some_new_kind")).effect == "allow"
