"""End-to-end tests for scripts/build-and-install.sh's staleness repair (ADR 0006).

WHY THIS HARNESS EXISTS. The script was entirely untested, and the defect it now
carries a repair for (issue #10) was a permanent wedge on a live host: pip decides
"already installed" from .dist-info METADATA alone, so a distribution whose files
are gone is never reinstalled, and the Agent's post-install step then dies on the
missing package directory. That is a shell-level branching bug -- no amount of
coverage over `datadog_checks/` could have caught it.

WHAT IS REAL AND WHAT IS STUBBED, because the distinction is the whole value:

  * REAL -- the script itself, and the probe's Python. The sandbox interpreter
    execs a genuine `python -S` against a synthetic site-packages that each test
    builds, so `importlib.metadata` and `importlib.util.find_spec` do the real
    work and the four states are reached the way a host reaches them. A test that
    stubbed the probe's answer would only pin the `case` statement and would have
    been just as green before the fix.
  * STUBBED -- `sudo` (execs, ignoring -u), `datadog-agent`, `python -m build`,
    and `python -m pip`. All four either need privileges we do not have or would
    mutate the machine running the tests. Each records its argv to a log the
    assertions read, so ORDER is observable, not just occurrence.

`-S` on the sandbox interpreter is load-bearing: without it the probe would find
the repository's own editable `uwsgi-stats` install in the test environment's
site-packages, and the `absent` case could never be reached.
"""

import os
import pwd
import shutil
import subprocess
import sys
import textwrap

import pytest

HERE = os.path.dirname(__file__)
SCRIPT = os.path.join(HERE, os.pardir, "scripts", "build-and-install.sh")

DIST_INFO = "uwsgi_stats-1.1.0.dist-info"
METADATA = "Metadata-Version: 2.1\nName: uwsgi-stats\nVersion: 1.1.0\n"


def _write(path, content, executable=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(content)
    if executable:
        os.chmod(path, 0o755)


@pytest.fixture
def sandbox(tmp_path):
    """A throwaway repo root, stub PATH, and an empty synthetic site-packages."""
    root = str(tmp_path)
    binp = os.path.join(root, "bin")
    sitep = os.path.join(root, "sitepkgs")
    log = os.path.join(root, "calls.log")
    os.makedirs(sitep)

    # The script resolves REPO_ROOT from its own location, so copying it here
    # sandboxes dist/ and everything else it writes.
    os.makedirs(os.path.join(root, "scripts"))
    shutil.copy(SCRIPT, os.path.join(root, "scripts", "build-and-install.sh"))

    _write(
        os.path.join(binp, "sudo"),
        '#!/usr/bin/env bash\n# Drop "-u <user>" and run the rest as ourselves.\n'
        'if [ "$1" = "-u" ]; then shift 2; fi\nexec "$@"\n',
        executable=True,
    )
    _write(
        os.path.join(binp, "datadog-agent"),
        f'#!/usr/bin/env bash\necho "AGENT $*" >> "{log}"\n'
        '# `integration show <legacy>` must fail so the legacy retire is a no-op.\n'
        'if [ "$1" = "integration" ] && [ "$2" = "show" ]; then exit 1; fi\nexit 0\n',
        executable=True,
    )
    _write(
        os.path.join(binp, "build-python"),
        f'#!/usr/bin/env bash\necho "BUILD $*" >> "{log}"\n'
        'if [ "$2" = "build" ] && [ "$3" = "--wheel" ]; then\n'
        '  mkdir -p dist && : > dist/uwsgi_stats-1.1.0-py3-none-any.whl\nfi\nexit 0\n',
        executable=True,
    )
    # The Agent's embedded interpreter: real Python for the probe, stubbed for pip.
    _write(
        os.path.join(binp, "embedded-python"),
        f'#!/usr/bin/env bash\n'
        f'if [ "$1" = "-m" ] && [ "$2" = "pip" ]; then echo "PIP $*" >> "{log}"; exit 0; fi\n'
        f'export PYTHONPATH="{sitep}"\nexec "{sys.executable}" -S "$@"\n',
        executable=True,
    )

    return {"root": root, "bin": binp, "site": sitep, "log": log}


def _install_metadata(sandbox):
    _write(os.path.join(sandbox["site"], DIST_INFO, "METADATA"), METADATA)


def _install_package(sandbox, with_data=True):
    pkg = os.path.join(sandbox["site"], "datadog_checks", "uwsgi_stats")
    _write(os.path.join(sandbox["site"], "datadog_checks", "__init__.py"), "")
    _write(os.path.join(pkg, "__init__.py"), "")
    if with_data:
        _write(os.path.join(pkg, "data", "conf.yaml.example"), "instances: []\n")


def _run(sandbox, embedded_python=None):
    env = dict(os.environ)
    env["PATH"] = sandbox["bin"] + os.pathsep + env["PATH"]
    env["PYTHON"] = os.path.join(sandbox["bin"], "build-python")
    env["DATADOG_AGENT"] = os.path.join(sandbox["bin"], "datadog-agent")
    env["DD_AGENT_USER"] = pwd.getpwuid(os.getuid()).pw_name
    env["DD_CONFD"] = os.path.join(sandbox["root"], "confd")
    env["DD_EMBEDDED_PYTHON"] = embedded_python or os.path.join(sandbox["bin"], "embedded-python")
    proc = subprocess.run(
        ["bash", os.path.join(sandbox["root"], "scripts", "build-and-install.sh")],
        env=env,
        capture_output=True,
        text=True,
    )
    calls = []
    if os.path.exists(sandbox["log"]):
        with open(sandbox["log"]) as fh:
            calls = [line.strip() for line in fh if line.strip()]
    return proc, calls


def _uninstalls(calls):
    return [c for c in calls if c.startswith("PIP ") and "uninstall" in c]


def _installs(calls):
    return [c for c in calls if c.startswith("AGENT ") and "integration install" in c]


# --- the four states -------------------------------------------------------


def test_absent_distribution_is_not_uninstalled(sandbox):
    """A fresh host: no metadata, nothing to clear, install still runs."""
    proc, calls = _run(sandbox)
    assert proc.returncode == 0, proc.stderr
    assert "not installed; nothing to clear" in proc.stdout
    assert _uninstalls(calls) == []
    assert len(_installs(calls)) == 1


def test_intact_installation_is_left_alone(sandbox):
    """The happy path must not churn a working install."""
    _install_metadata(sandbox)
    _install_package(sandbox)
    proc, calls = _run(sandbox)
    assert proc.returncode == 0, proc.stderr
    assert "is installed and intact" in proc.stdout
    assert _uninstalls(calls) == []
    assert len(_installs(calls)) == 1


def test_orphaned_metadata_is_cleared_before_the_install(sandbox):
    """Issue #10: metadata present, package gone. Order matters, so assert it."""
    _install_metadata(sandbox)
    proc, calls = _run(sandbox)
    assert proc.returncode == 0, proc.stderr
    assert "its files are gone" in proc.stdout

    uninstall = _uninstalls(calls)
    assert len(uninstall) == 1
    assert "uwsgi-stats" in uninstall[0]
    # Clearing metadata AFTER the install would leave the host with nothing.
    assert calls.index(uninstall[0]) < calls.index(_installs(calls)[0])


def test_package_without_its_data_directory_counts_as_orphaned(sandbox):
    """`data/` is the path the Agent's post-install step opens; its loss wedges too."""
    _install_metadata(sandbox)
    _install_package(sandbox, with_data=False)
    proc, calls = _run(sandbox)
    assert proc.returncode == 0, proc.stderr
    assert len(_uninstalls(calls)) == 1


def test_unreadable_interpreter_is_a_no_op_not_a_removal(sandbox):
    """An unreadable probe is not evidence. It must not remove and must not fail."""
    proc, calls = _run(sandbox, embedded_python=os.path.join(sandbox["root"], "nope", "python"))
    assert proc.returncode == 0, proc.stderr
    assert "skipping the staleness check" in proc.stdout
    assert _uninstalls(calls) == []
    assert len(_installs(calls)) == 1


# --- the ordering constraint the repair depends on -------------------------


def test_the_probe_runs_after_the_legacy_retire(sandbox):
    """The legacy retire can CREATE the orphan, so probing before it sees a lie.

    Both distributions own the same datadog_checks/uwsgi_stats/ files, so
    removing the legacy one deletes them and orphans this one's metadata --
    within a single run. Pinned on the script's own output ordering rather than
    by re-reading the file, so a reordering fails here.
    """
    _install_metadata(sandbox)
    proc, _ = _run(sandbox)
    assert proc.returncode == 0, proc.stderr
    retire = proc.stdout.index("Checking for the legacy")
    staleness = proc.stdout.index("Checking for stale")
    install = proc.stdout.index("Installing into the Agent's embedded Python")
    assert retire < staleness < install


def test_a_failed_metadata_clear_stops_the_run(sandbox):
    """Proceeding would hit the exact skip-then-ENOENT this repair exists to avoid."""
    _install_metadata(sandbox)
    _write(
        os.path.join(sandbox["bin"], "embedded-python"),
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            if [ "$1" = "-m" ] && [ "$2" = "pip" ]; then exit 1; fi
            export PYTHONPATH="{sandbox["site"]}"
            exec "{sys.executable}" -S "$@"
            """
        ),
        executable=True,
    )
    proc, calls = _run(sandbox)
    assert proc.returncode != 0
    assert "could not clear the stale" in proc.stderr
    assert _installs(calls) == []
