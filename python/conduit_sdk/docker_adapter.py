"""v1 Docker sandbox adapter (seed SOURCE.md \u00a714 "v1: Local Docker sandbox").

Runs an agent *inside* a Docker container and streams its normalized events back
to the host. It is a thin wrapper over :func:`conduit_sdk.runlayer.process_adapter`:
the container's stdout is parsed as newline-delimited JSON ``AgentEvent`` records,
exactly as the local fake-process adapter does. Docker supplies the four v1
guarantees from the seed:

* **mount workspace**            -> ``-v {workspace}:{work_dir}``
* **run adapter inside sandbox** -> the container ``command``
* **stream events to host**      -> the container's stdout NDJSON
* **cleanup sandbox**            -> ``--rm``

Because the normalized event stream is identical to an in-process run of the same
agent, ``Run`` / ``Runner`` / ``Result`` are unchanged whether the agent runs
locally or in a container (the seed's with/without-sandbox event-stability
invariant). :func:`hardening_args` returns the ``docker run`` flags that lock the
sandbox down (no network, read-only root, resource caps, dropped capabilities).
"""

from __future__ import annotations

from collections.abc import Sequence

from conduit_sdk.runlayer import Adapter, process_adapter

__all__ = ["docker_adapter", "hardening_args"]


def hardening_args(
    *,
    network: bool = False,
    read_only_root: bool = True,
    tmpfs: Sequence[str] = ("/tmp",),
    memory: str | None = "512m",
    cpus: str | None = "1.0",
    pids_limit: int | None = 256,
    drop_all_caps: bool = True,
    no_new_privileges: bool = True,
    user: str | None = None,
) -> list[str]:
    """Return ``docker run`` flags that harden the sandbox.

    Safe defaults for an agent that only touches its mounted workspace: no
    network egress, a read-only root filesystem (the ``-v`` workspace mount stays
    writable), a ``tmpfs`` ``/tmp``, memory/CPU/PID caps, all Linux capabilities
    dropped, and ``no-new-privileges``. ``user`` (e.g. ``"1000:1000"``) is opt-in
    because non-root writes to a bind mount are environment-specific.

    Pass the result as ``docker_adapter(..., extra_run_args=hardening_args())``.
    """
    args: list[str] = []
    if not network:
        args += ["--network", "none"]
    if read_only_root:
        args.append("--read-only")
    for mount in tmpfs:
        args += ["--tmpfs", mount]
    if memory:
        args += ["--memory", memory]
    if cpus:
        args += ["--cpus", cpus]
    if pids_limit is not None:
        args += ["--pids-limit", str(pids_limit)]
    if drop_all_caps:
        args += ["--cap-drop", "ALL"]
    if no_new_privileges:
        args += ["--security-opt", "no-new-privileges"]
    if user:
        args += ["--user", user]
    return args


def docker_adapter(
    image: str,
    command: Sequence[str],
    *,
    workspace: str,
    work_dir: str = "/work",
    platform: str | None = None,
    extra_run_args: Sequence[str] | None = None,
    source: str = "adapter",
) -> Adapter:
    """Build an :class:`Adapter` that runs *command* inside a Docker container.

    Parameters
    ----------
    image:
        Docker image to run. Must contain whatever *command* needs.
    command:
        Argv executed inside the container. It MUST emit newline-delimited JSON
        ``AgentEvent`` records on stdout (the same contract as ``process_adapter``);
        ``run.started`` / ``run.completed`` are synthesized from the process
        lifecycle, so the command should emit only the in-between events.
    workspace:
        Host path bind-mounted at *work_dir* (the agent's working tree).
    work_dir:
        Mount point and working directory inside the container (default ``/work``).
    platform:
        Optional ``--platform`` value (e.g. ``"linux/amd64"``).
    extra_run_args:
        Extra args inserted right after ``docker run --rm`` (e.g. the output of
        :func:`hardening_args`, or ``["--network", "none"]``).
    source:
        ``AgentEvent.source`` stamped on synthesized lifecycle events.
    """
    argv: list[str] = ["docker", "run", "--rm"]
    if platform:
        argv += ["--platform", platform]
    if extra_run_args:
        argv += list(extra_run_args)
    argv += ["-v", f"{workspace}:{work_dir}", "-w", work_dir, image, *command]
    return process_adapter(argv, source=source)
