"""Autoinstall — a two-agent plan-then-verify-by-execution workflow.

Stage 1 (planner): an agent explores a repo checkout and writes a structured
``setup-manifest.json`` (stack, setup steps, target verify commands, expected
outcome, anticipated mocks).

Stage 2 (executor + verifier): a FRESH agent gets the repo + the target
commands, installs/mocks whatever is needed so they run, and the orchestrator
re-runs each target command to verify (exit code + fuzzy output match). On
failure the executor is retried (up to *max_attempts*) with the failure output
fed back. Inspired by Composer autoinstall
(https://cursor.com/blog/bootstrapping-composer-with-autoinstall).

Local-first: both agents are launched via :func:`conduit_sdk.runlayer.acp_agent`
(default ``["omp", "acp"]``). The executor runs sandboxed in a tempdir copy;
its permission bridge auto-allows (no policy) and the verifier is the
orchestrator's own subprocess check.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from conduit_sdk.elicitation import ElicitationResponse
from conduit_sdk.runlayer import Agent, Runner, acp_agent
from conduit_sdk.tools import _validate_against_schema


_SANDBOX_IGNORE = shutil.ignore_patterns(
    "node_modules", ".git", ".next", ".turbo", "dist", "build",
    ".cache", ".venv", "target", "__pycache__",
)


def _cow_copytree(src: str, dst: str) -> None:
    """Copy *src* → *dst* with copy-on-write where available.

    On macOS (APFS) uses clonefile(2) via ``cp -cR`` — instant and ~zero extra
    disk (data blocks are shared; only a write costs space), so vendored trees
    like ``node_modules`` are free to "copy" and no ignore-pattern is needed.
    Falls back to :func:`shutil.copytree` (with the heavy-dir ignore) when
    clonefile is unavailable or *src*/*dst* sit on different volumes.
    """
    if platform.system() == "Darwin":
        result = subprocess.run(
            ["cp", "-cR", src, dst], capture_output=True, text=True,
        )
        if result.returncode == 0 and os.path.isdir(dst):
            return
        # clonefile failed (cross-volume / non-APFS) — fall through to copytree.
    shutil.copytree(src, dst, ignore=_SANDBOX_IGNORE)
__all__ = ["autoinstall", "AutoinstallResult", "VerifyOutcome"]

_MANIFEST_NAME = "setup-manifest.json"

# Condensed schema the planner must satisfy (full SSOT: schema/autoinstall_manifest.schema.json).
_MANIFEST_SPEC = """\
Write setup-manifest.json at the repo root with EXACTLY this shape (camelCase,
omit fields you don't use except the required ones):

{
  "projectName": "string (required)",
  "stack": ["python", "rust", ...],
  "setupSteps": [ {"kind": "install|script|env|mock", "command": "...", "description": "..."} ],
  "targetCommands": [ {"command": "... (required)", "expectedExit": 0, "expectedOutputContains": "substring"} ],
  "expectedOutcome": "one paragraph: what a correctly-set-up env looks like (required)",
  "mocks": ["anticipated mocks, e.g. fake DB user, placeholder config"]
}

Rules: targetCommands must have >= 1 entry; each command is a single shell line.
Write ONLY the file, then reply with a one-line summary."""

_STAGE1_TASK = (
    "You are setting up a codebase to be runnable. Your cwd is a copy of the "
    "target repo. Explore it (README, Makefile, pyproject.toml/package.json/"
    "Cargo.toml, CI configs, lockfiles). Determine how to install and run it, "
    "and imagine what a correctly-set-up environment looks like.\n\n"
    f"{_MANIFEST_SPEC}\n\n"
    "Propose setupSteps (ordered hints), at least 1 targetCommand with "
    "expectedExit + expectedOutputContains, expectedOutcome, and any mocks you "
    "anticipate. Be concrete and grounded in what you actually read."
)

_STAGE2_TASK = (
    "You are in a FRESH sandbox copy of a repo (your cwd). Make these TARGET "
    "COMMANDS run successfully — install deps, configure, mock anything missing "
    "(do not modify files outside cwd):\n\n"
    "{targets}\n\n"
    "Setup hints you may use:\n{hints}\n\n"
    "Goal context: {outcome}\n"
    "{prior_failures}"
    "\nWork until every target command passes, then reply with a one-line summary."
)


@dataclass
class VerifyOutcome:
    command: str
    passed: bool
    exit_code: int | None
    stdout: str
    expected_output_contains: str | None


@dataclass
class AutoinstallResult:
    repo_path: str
    verified: bool
    attempts: int
    manifest: dict | None = None
    stage1: Any = None  # Result
    stage2: Any = None  # Result (last attempt)
    verify: list[VerifyOutcome] = field(default_factory=list)
    workdir: str | None = None
    error: str | None = None


def _default_spec() -> list[str] | None:
    if shutil.which("omp"):
        return ["omp", "acp"]
    return None


def _default_factory(spec: Any, *, cwd: str, timeout: float,
                     enable_elicitation: bool, **_: Any) -> Any:
    from conduit_sdk.options import AgentOptions

    return acp_agent(
        spec,
        options=AgentOptions(cwd=cwd),
        timeout=int(timeout),
        coalesce_messages=True,
        include_thoughts=False,
        enable_elicitation=enable_elicitation,
    )


def _load_manifest(cwd: str) -> dict | None:
    path = os.path.join(cwd, _MANIFEST_NAME)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


_SCHEMA_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "schema", "autoinstall_manifest.schema.json",
)


def _validate_manifest(manifest: dict) -> list[str]:
    try:
        with open(_SCHEMA_PATH) as fh:
            schema = json.load(fh)
    except OSError:
        return ["manifest schema unreadable"]
    return _validate_against_schema(manifest, schema, path="manifest")


def _verify(targets: list[dict], cwd: str) -> list[VerifyOutcome]:
    out: list[VerifyOutcome] = []
    for t in targets:
        cmd = t.get("command", "")
        expected_exit = t.get("expectedExit", 0)
        expected_contains = t.get("expectedOutputContains")
        try:
            proc = subprocess.run(
                cmd, shell=True, cwd=cwd, capture_output=True, text=True,
                timeout=180,
            )
            stdout = proc.stdout
            exit_code = proc.returncode
        except subprocess.TimeoutExpired:
            stdout = "<timed out after 180s>"
            exit_code = None
        passed = exit_code == expected_exit
        if passed and expected_contains is not None:
            passed = expected_contains in stdout
        out.append(VerifyOutcome(
            command=cmd, passed=passed, exit_code=exit_code,
            stdout=stdout[-2000:], expected_output_contains=expected_contains,
        ))
    return out


async def autoinstall(
    repo_path: str | Path,
    *,
    agent_spec: Any = None,
    adapter_factory: Callable[..., Any] | None = None,
    max_attempts: int = 3,
    timeout: float = 600,
    workdir: str | Path | None = None,
    on_event: Callable[..., None] | None = None,
    enable_elicitation: bool = True,
    keep_workdir: bool = False,
) -> AutoinstallResult:
    """Run the two-stage autoinstall workflow against *repo_path*.

    Returns an :class:`AutoinstallResult`. ``verified`` is True iff every
    target command passed within *max_attempts* Stage-2 attempts.
    """
    repo_path = str(repo_path)
    spec = agent_spec or _default_spec()
    factory = adapter_factory or _default_factory
    if spec is None and adapter_factory is None:
        return AutoinstallResult(
            repo_path=repo_path, verified=False, attempts=0,
            error="no agent: install `omp` or pass agent_spec / adapter_factory",
        )

    owns_workdir = workdir is None
    if owns_workdir:
        import tempfile
        workdir = tempfile.mkdtemp(prefix="autoinstall-")
    workdir = str(workdir)
    stage1_cwd = os.path.join(workdir, "stage1")
    stage2_cwd = os.path.join(workdir, "stage2")

    def _decline(_req: Any) -> ElicitationResponse:
        return ElicitationResponse(action="decline")

    try:
        # ---- Stage 1: planner writes the manifest ----
        _cow_copytree(repo_path, stage1_cwd)
        s1_adapter = factory(spec, cwd=stage1_cwd, timeout=timeout,
                             enable_elicitation=enable_elicitation)
        s1_run = await Runner.start(
            Agent(name="planner"), task=_STAGE1_TASK, adapter=s1_adapter,
            on_elicitation=_decline, timeout=timeout,
        )
        async for ev in s1_run.events():
            if on_event:
                on_event(ev, stage=1, attempt=1)
        s1_result = await s1_run.result()

        manifest = _load_manifest(stage1_cwd)
        if manifest is None:
            return AutoinstallResult(
                repo_path=repo_path, verified=False, attempts=0,
                stage1=s1_result, workdir=workdir if keep_workdir else None,
                error="planner did not write setup-manifest.json",
            )
        errors = _validate_manifest(manifest)
        if errors:
            return AutoinstallResult(
                repo_path=repo_path, verified=False, attempts=0, manifest=manifest,
                stage1=s1_result, workdir=workdir if keep_workdir else None,
                error="manifest invalid: " + "; ".join(errors),
            )

        targets = manifest.get("targetCommands", [])
        hints = manifest.get("setupSteps", [])
        outcome = manifest.get("expectedOutcome", "")

        # ---- Stage 2: executor + verify, with retry ----
        _cow_copytree(repo_path, stage2_cwd)
        verified = False
        last_verify: list[VerifyOutcome] = []
        s2_result = None
        failures: list[str] = []
        attempt = 0
        for attempt in range(1, max_attempts + 1):
            s2_adapter = factory(spec, cwd=stage2_cwd, timeout=timeout,
                                 enable_elicitation=enable_elicitation)
            task = _STAGE2_TASK.format(
                targets="\n".join(f"- `{t.get('command', '')}`" for t in targets),
                hints="\n".join(f"- {h.get('command', '')}" for h in hints) or "(none)",
                outcome=outcome,
                prior_failures=(
                    "\nPrevious attempts FAILED — debug from this output:\n"
                    + "\n".join(failures) + "\n" if failures else ""
                ),
            )
            s2_run = await Runner.start(
                Agent(name="executor"), task=task, adapter=s2_adapter,
                on_elicitation=_decline, timeout=timeout,
            )
            async for ev in s2_run.events():
                if on_event:
                    on_event(ev, stage=2, attempt=attempt)
            s2_result = await s2_run.result()

            last_verify = _verify(targets, stage2_cwd)
            if all(v.passed for v in last_verify):
                verified = True
                break
            failures = [
                f"- `{v.command}` exited {v.exit_code} (expected "
                f"{v.expected_output_contains!r}); tail: {v.stdout[-300:]!r}"
                for v in last_verify if not v.passed
            ]

        return AutoinstallResult(
            repo_path=repo_path, verified=verified, attempts=attempt,
            manifest=manifest, stage1=s1_result, stage2=s2_result,
            verify=last_verify,
            workdir=workdir if keep_workdir else None,
        )
    finally:
        if owns_workdir and not keep_workdir:
            shutil.rmtree(workdir, ignore_errors=True)
