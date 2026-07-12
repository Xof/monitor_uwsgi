---
id: 0003
title: Provide a build-and-install helper script for Agent installation
date: 2026-07-11
status: Accepted
summary: Ship scripts/build-and-install.sh to run the wheel build, dd-agent install, and conf.d staging as one command, encoding the cross-user permission dance the manual install path leaves to the operator.
---

# 0003. Provide a build-and-install helper script for Agent installation

## Context

ADR 0002 settled on the wheel install path: `python -m build` →
`datadog-agent integration install -w dist/*.whl`. In practice that one line
hides a cross-user, multi-privilege dance that every operator rediscovers the
hard way:

- The wheel is **built by the operator** but **installed by `dd-agent`** (the
  Agent runs `integration install` as its own user). `dd-agent` cannot read a
  wheel sitting under the operator's `$HOME` (mode `700`/`750`) — the classic
  `[Errno 13] Permission denied`. The wheel has to be staged somewhere
  world-readable, and `$TMPDIR` is not reliably that (it may be a private
  per-user directory).
- Config lives under `/etc/datadog-agent`, typically readable only by
  `dd-agent`, so probing/creating `conf.d/uwsgi_stats.d/conf.yaml` also has to
  happen as that user.
- Only one step — restarting the Agent — actually needs root; the rest needs
  nothing more than the ability to `sudo -u dd-agent`.
- Re-running to *upgrade* the check must not clobber an existing `conf.yaml`
  (it holds the operator's `stats_url`).

Left as README prose (as it was), each of these is a papercut that recurs for
every new operator and is easy to get subtly wrong.

## Decision

We will ship `scripts/build-and-install.sh`, a stdlib-only Bash script
(`set -euo pipefail`) that runs the whole flow as a single command from an
operator account permitted to `sudo -u dd-agent`, needing **no general root
access**:

1. **Preflight** — verify `python -m build`, the `datadog-agent` binary, the
   `dd-agent` user, and `sudo -u dd-agent` all work, and fail with an
   actionable message before changing anything.
2. **Build** the wheel and select the newest `dist/datadog_uwsgi_stats-*.whl`.
3. **Stage** the wheel into a `chmod 755` scratch directory under `/tmp`
   (deliberately `/tmp`, not `$TMPDIR`) and install it as `dd-agent`; the
   scratch dir is removed on exit.
4. **Configure** — create `conf.yaml` from the packaged example unless one
   already exists (never overwritten).
5. **Print, not perform**, the final operator steps: set `stats_url`, verify,
   and restart the Agent (the sole root step).

Host-specific values are overridable via environment: `PYTHON`,
`DD_AGENT_USER`, `DD_CONFD`, `DATADOG_AGENT`. The README documents this as the
recommended install path and retains the manual steps as a fallback.

## Alternatives considered

- **Leave install as README-only manual steps** — Rejected as the primary path.
  The `/tmp` staging and `dd-agent` ownership rules are exactly the kind of
  non-obvious, repeatable knowledge a script should encode once instead of
  asking every operator to re-derive from an error message. The manual steps
  are kept in the README for hosts the script's assumptions don't fit.
- **A Python console-entry-point shipped inside the wheel** — Rejected. The
  bootstrap must run *as the operator, before the wheel is installed*, and it
  installs *into* `dd-agent`'s embedded 3.8 environment; a build/install tool
  cannot live inside the artifact it produces and installs. Bash with explicit
  `sudo -u` hops matches the real privilege boundaries better than Python here.
- **Run the whole thing as root** — Rejected. Only the Agent restart needs
  root. Escalating the build/install/config to root is unnecessary privilege
  and would `chown` the wheel-build and config artifacts to the wrong user.

## Consequences

- New operators get a one-command install; the surprising parts (`/tmp`
  staging, the no-clobber `conf.yaml` guard, the operator-vs-`dd-agent`-vs-root
  split) are encoded once and explained in both the script header and the README.
- The script assumes a **systemd** host and a sudo-to-`dd-agent` operator
  account. Non-systemd hosts, a different Agent user, or a non-PATH binary are
  handled via the env overrides or the manual path — but they are assumptions,
  not universals.
- It is another artifact coupled to the packaging/config layout: the `conf.d`
  path and the `datadog_uwsgi_stats-*.whl` name are duplicated between the
  script, the README, and the packaging metadata. If those change, the script
  and its docs must follow (see the "Where to change X" note in
  `ARCHITECTURE.md`).
- The script never restarts the Agent, so the check is installed but **not yet
  reporting** when it finishes; the operator's explicit final step is still
  required. This is intentional (root boundary) but means "ran the script" ≠
  "metrics flowing."
