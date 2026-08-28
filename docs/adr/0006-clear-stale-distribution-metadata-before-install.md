---
id: 0006
title: Clear stale uwsgi-stats metadata before installing, and probe for it after the legacy retire
date: 2026-08-27
status: Accepted
summary: Detect a distribution whose .dist-info outlived its files and clear it with embedded pip before the install, because pip's "already installed" test reads metadata only and turns that state into a permanent wedge no re-run can repair.
---

# 0006. Clear stale uwsgi-stats metadata before installing, and probe for it after the legacy retire

## Context

`datadog-agent integration install` delegates to the Agent's embedded pip, and
pip decides whether a distribution is already installed by reading its
`.dist-info` metadata. It does not verify that the files that metadata describes
still exist. A distribution whose metadata outlived its files is therefore
reported as installed, the install is skipped, and the command returns success.

The Agent then runs its post-install step of copying the integration's
`data/conf.yaml.example` into `conf.d/`, opens a package directory that was
never written, and fails:

```
uwsgi-stats is already installed with the same version as the provided wheel.
Use --force-reinstall to force an installation of the wheel.
...
Error: Some errors prevented moving uwsgi-stats configuration files:
open .../site-packages/datadog_checks/uwsgi_stats/data: no such file or directory
```

Observed on a live host on 2026-08-27 (issue #10): `uwsgi_stats-1.1.0.dist-info`
present, `datadog_checks/uwsgi_stats/` absent, `conf.d/uwsgi_stats.d/` intact,
and `datadog-agent check uwsgi_stats` reporting `No module named
'datadog_checks.uwsgi_stats'`. Every subsequent run failed identically. The host
could not repair itself, and `scripts/build-and-install.sh` — whose entire
purpose is to make the installed check equal this source tree — was the thing
that could not do it.

Neither obvious repair exists. The Agent CLI accepts only `-w/--local-wheel`,
`-t/--third-party` and `--unsafe-disable-verification`; the `--force-reinstall`
in the output above is pip's advice about a flag the Agent does not expose. And
`integration remove` refuses the distribution outright — *"this manager only
handles datadog packages"* — because ADR 0004 dropped the `datadog-` prefix its
validator requires. That consequence was already recorded in the script's
legacy-retire comment ("the new name does not, which is why removing `$DIST`
later needs embedded pip instead"); this record is that sentence becoming code.

The state is also reachable from inside this script. `datadog-uwsgi-stats` and
`uwsgi-stats` install the same `datadog_checks/uwsgi_stats/` files — the reason
ADR 0004's retire runs before the install rather than after it. So a run that
finds both present removes the legacy distribution, deletes the shared files,
and leaves the surviving distribution's metadata pointing at nothing, all before
reaching the install that then skips.

## Decision

Add a step between the legacy retire and the install that identifies stale
metadata and clears it with the Agent's embedded pip.

The probe reports **four** states, not two: `installed`, `absent`, `orphan`, and
`unknown`. Only `orphan` removes anything. `unknown` — the embedded interpreter
is absent or will not run — is a no-op, because an unreadable probe is not
evidence, and the rest of the script works on a host whose layout differs.

`orphan` is reported when the distribution's metadata is present and either the
check package is not importable or its `data/` subdirectory is missing. Both are
"pip believes something untrue", and the second is literally the path the
Agent's post-install step opens. The bias is deliberate: a false `orphan` costs
one reinstall, since the install follows immediately; a false `installed` costs
the wedge this record exists to remove.

The probe runs **after** the legacy retire, because the retire is one of the
things that creates the state it looks for.

The interpreter is `DD_EMBEDDED_PYTHON`, defaulting to
`/opt/datadog-agent/embedded/bin/python` and overridable like the script's four
existing knobs. The probe source is passed inline with `-c` rather than kept in
a `scripts/*.py` file: it runs as the Agent user, which per the wheel-staging
note in the script header normally cannot read anything under the operator's
`$HOME`, so a repository file would reproduce the permission failure that
staging exists to avoid. Its arguments are passed as `argv`, not interpolated.

A failed clear stops the run rather than continuing, because at that point the
install is known to be about to skip and then fail on the missing directory.

## Alternatives considered

**Uninstall unconditionally before every install.** Simpler, no probe, and it
would additionally fix a second latent problem: because the version is a
constant and there is no force flag, a change to this repository that does not
bump `__about__.py` never reaches a host where the check is already installed.
Rejected because it is destructive on the happy path — if the install then
fails, the host is left with no check at all, where today it keeps a working
one. That trade is worth making only for a problem that is actually biting, and
this one is not: the installed `check.py` on a healthy host is byte-identical to
the clone. Fixing the unbumped-version case is a separate decision, and bumping
the version is its cheaper answer.

**Probe before the legacy retire.** Reads more naturally as "check the world,
then act", and is wrong: the retire can create the orphan inside the same run,
so an earlier probe would correctly observe a host that was still healthy and
skip the repair the run itself was about to need.

**Detect only that the package is unimportable, ignoring `data/`.** Matches what
"the check works" means and is the signal the deploy consumer's own probe uses.
Rejected as the sole test because the failure being repaired is the installer's,
and its post-install step opens `data/` specifically.

**Fix it in the deploy consumer instead.** The out-of-repo Ansible role that
runs this script already detects the state correctly — it probes the Agent's
import path and rebuilds. Its repair is this script, so the gap is here.
Clearing another project's `site-packages` from a configuration-management role
would also split the knowledge across two repositories.

**Keep the shell untested.** Rejected: this script had no coverage at all, and
the defect was pure shell-level branching that no amount of coverage over
`datadog_checks/` could reach. `tests/test_build_and_install.py` runs the real
script and the real probe against a synthetic `site-packages`, stubbing only
`sudo`, `datadog-agent`, `python -m build` and `python -m pip`.

## Consequences

A host in the wedged state repairs itself on the next run. A healthy host does
strictly nothing new beyond one probe.

The script now depends on locating the Agent's embedded interpreter, which it
previously did not need. That dependency is soft by construction — an
unlocatable interpreter degrades to the previous behaviour rather than failing —
so a layout this default does not anticipate loses the repair, not the install.

The unbumped-version staleness described above remains. It is recorded in issue
#10 and is not addressed here.

`tests/test_build_and_install.py` pins the four states, the ordering against the
legacy retire, and the refusal to proceed after a failed clear. All seven fail
against the script as it stood before this record, and the mutation that turns
`unknown` into `orphan` is caught by exactly one of them.
