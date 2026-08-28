#!/usr/bin/env bash
#
# build-and-install.sh — build the uwsgi_stats integration wheel and install it
# into the Datadog Agent's embedded Python, then stage its configuration.
#
# Run this from an operator account that is allowed to `sudo -u dd-agent` (the
# Datadog Agent user). It needs NO general root access: the wheel build, the
# install, the config copy, and the verify step all run either as you or as the
# Agent user. Restarting the Agent — the one step that needs root — is left to
# you and printed at the end.
#
# Non-obvious choices, so a future reader doesn't "fix" them into breakage:
#   * The wheel is staged into /tmp before installing. `datadog-agent integration
#     install` runs as dd-agent, which keeps the current directory and normally
#     cannot read files under your $HOME (mode 700/750) — the classic
#     "[Errno 13] Permission denied" on the wheel. /tmp is world-traversable, so
#     dd-agent can read a copy there. We deliberately use /tmp, not $TMPDIR,
#     which may itself be a private per-user directory dd-agent cannot enter.
#   * An existing conf.yaml is never overwritten: it holds your stats_url, and
#     re-running this to upgrade the check must not wipe your configuration.
#   * conf.d files are probed/created via `sudo -u dd-agent` because
#     /etc/datadog-agent is typically readable only by the Agent user.
#   * The distribution is named `uwsgi-stats`, with no `datadog-` prefix, so that
#     the Agent's upgrade restore reinstalls it from PyPI via embedded pip rather
#     than from Datadog's TUF repo, which has never heard of it (ADR 0004). The
#     check name is still `uwsgi_stats`; only the PyPI distribution name changed.
#   * The legacy `datadog-uwsgi-stats` distribution is removed BEFORE the new
#     wheel is installed, never after. Both own the same files, so removing it
#     afterwards would delete the install we just performed.
#   * Stale `uwsgi-stats` metadata is cleared before installing, and the probe for
#     it runs AFTER the legacy retire above -- because that retire is one of the
#     things that can create it. See the step's own comment (ADR 0006).
#
# Overridable via environment:
#   PYTHON          python used to build the wheel   (default: python3)
#   DD_AGENT_USER   the Datadog Agent user           (default: dd-agent)
#   DD_CONFD        Agent conf.d directory           (default: /etc/datadog-agent/conf.d)
#   DATADOG_AGENT   path to the datadog-agent binary (default: found on PATH)
#   DD_EMBEDDED_PYTHON  the Agent's embedded interpreter
#                   (default: /opt/datadog-agent/embedded/bin/python)
#
set -euo pipefail

PYTHON="${PYTHON:-python3}"
DD_AGENT_USER="${DD_AGENT_USER:-dd-agent}"
DD_CONFD="${DD_CONFD:-/etc/datadog-agent/conf.d}"
DD_EMBEDDED_PYTHON="${DD_EMBEDDED_PYTHON:-/opt/datadog-agent/embedded/bin/python}"
CHECK="uwsgi_stats"
DIST="uwsgi-stats"                 # PyPI distribution name (see header)
LEGACY_DIST="datadog-uwsgi-stats"  # pre-1.1.0 name, retired by ADR 0004

usage() {
  # Print the header comment block: line 2 through the last consecutive comment
  # line. Self-locating on purpose -- the previous hardcoded `sed -n '2,29p'`
  # silently truncated --help every time the header gained a bullet.
  awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "$0"
  exit "${1:-0}"
}
case "${1:-}" in -h | --help) usage 0 ;; esac

die() { echo "error: $*" >&2; exit 1; }
step() { printf '\n==> %s\n' "$*"; }

# Resolve the repo root from this script's own location so it works from any CWD.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# --- Preflight -------------------------------------------------------------
step "Checking prerequisites"
"$PYTHON" -m build --version >/dev/null 2>&1 \
  || die "'$PYTHON -m build' is unavailable — install it with: $PYTHON -m pip install build"

AGENT_BIN="${DATADOG_AGENT:-$(command -v datadog-agent || true)}"
[ -n "$AGENT_BIN" ] || die "datadog-agent not found on PATH (set DATADOG_AGENT=/path/to/datadog-agent)"
[ -x "$AGENT_BIN" ] || die "datadog-agent binary not found or not executable: $AGENT_BIN"

id "$DD_AGENT_USER" >/dev/null 2>&1 || die "user '$DD_AGENT_USER' does not exist (set DD_AGENT_USER)"

sudo -u "$DD_AGENT_USER" true \
  || die "cannot 'sudo -u $DD_AGENT_USER' — run this from an account permitted to sudo to the Agent user"

# --- Build -----------------------------------------------------------------
step "Building the wheel"
"$PYTHON" -m build --wheel
WHEEL="$(ls -t dist/uwsgi_stats-*.whl 2>/dev/null | head -1 || true)"
[ -n "$WHEEL" ] || die "no wheel found in dist/ after build"
echo "built: $WHEEL"

# --- Stage where dd-agent can read it --------------------------------------
step "Staging the wheel for the Agent user"
STAGING="$(mktemp -d /tmp/uwsgi-stats-install.XXXXXX)"
trap 'rm -rf "$STAGING"' EXIT
chmod 755 "$STAGING"                          # dd-agent must be able to traverse it
cp "$WHEEL" "$STAGING/"
STAGED_WHEEL="$STAGING/$(basename "$WHEEL")"
chmod 644 "$STAGED_WHEEL"                      # ...and read the wheel

# --- Retire the pre-1.1.0 distribution -------------------------------------
# Both distributions install the SAME datadog_checks/uwsgi_stats/ files. If the
# old one is left in place, a later `pip uninstall datadog-uwsgi-stats` deletes
# the files this run installs -- so retire it here, ahead of the install, never
# after it. `integration show` is the probe because it exits non-zero when the
# package is absent; both subcommands accept the legacy name because it carries
# the `datadog-` prefix the Agent's CLI validates against (the new name does
# not, which is why removing $DIST later needs embedded pip instead).
# Removal is pip-level only: it does not touch conf.d, so conf.yaml survives.
step "Checking for the legacy '$LEGACY_DIST' distribution"
if sudo -u "$DD_AGENT_USER" "$AGENT_BIN" integration show "$LEGACY_DIST" >/dev/null 2>&1; then
  echo "found $LEGACY_DIST -- removing it before installing $DIST"
  sudo -u "$DD_AGENT_USER" "$AGENT_BIN" integration remove "$LEGACY_DIST"
else
  echo "not installed; nothing to retire"
fi

# --- Clear stale $DIST metadata --------------------------------------------
# `datadog-agent integration install` delegates to the embedded pip, and pip
# decides "already installed" from the .dist-info METADATA alone -- it never
# checks that the files are still there. So a distribution whose metadata
# outlived its files is a permanent wedge: pip skips the install and reports
# success, the Agent's post-install step then tries to copy
# datadog_checks/<check>/data/conf.yaml.example out of a package directory that
# does not exist, and the run dies with
#
#     Error: Some errors prevented moving uwsgi-stats configuration files:
#     open .../site-packages/datadog_checks/uwsgi_stats/data: no such file or directory
#
# Every later run fails identically. Neither obvious repair is available: the
# Agent CLI has no --force-reinstall (that advice in pip's output is pip's, not
# the Agent's), and `integration remove` refuses $DIST outright because ADR 0004
# dropped the `datadog-` prefix its validator requires -- which is exactly the
# case the legacy-retire comment above already anticipates. Embedded pip is the
# only tool that can clear it.
#
# AFTER THE LEGACY RETIRE, NOT BEFORE, and that ordering is load-bearing: both
# distributions own the same datadog_checks/uwsgi_stats/ files, so removing
# $LEGACY_DIST deletes them while leaving $DIST's .dist-info behind -- the retire
# step is itself one of the ways this state is created, within a single run.
# Probing ahead of it would look, correctly, at a host that was still healthy.
#
# FOUR STATES, NOT TWO. `unknown` -- the embedded interpreter is not where we
# expect, or cannot run -- must be a no-op, not a failure and not a removal: the
# rest of this script works fine on a host whose layout differs, and an
# unreadable probe is not evidence of anything. Only a positive identification
# of `orphan` removes anything. A false `orphan` costs one reinstall (we install
# immediately below); a false `installed` costs the wedge above, so the probe is
# deliberately biased toward `orphan` -- it also reports one when the package
# directory is present but has lost the `data/` subdirectory the Agent's
# post-install step opens.
#
# INLINE `-c`, NOT A FILE IN THIS REPO. The probe runs as $DD_AGENT_USER, which
# per the staging note in the header normally cannot read anything under your
# $HOME -- a scripts/*.py helper would hit the same "[Errno 13] Permission
# denied" the wheel staging exists to avoid. Arguments are passed as argv rather
# than interpolated into the source.
step "Checking for stale '$DIST' metadata"
DIST_STATE="$(sudo -u "$DD_AGENT_USER" "$DD_EMBEDDED_PYTHON" -c '
import importlib.util, os, sys
from importlib.metadata import PackageNotFoundError, version

# Both spellings: the distribution is "uwsgi-stats" but setuptools writes the
# directory as "uwsgi_stats-<v>.dist-info", and how much name normalization
# importlib.metadata does varies across the Python versions the Agent has
# embedded over time. Asking for both removes the dependency on that.
for candidate in (sys.argv[1], sys.argv[1].replace("-", "_")):
    try:
        version(candidate)
    except PackageNotFoundError:
        continue
    break
else:
    print("absent")
    raise SystemExit
try:
    spec = importlib.util.find_spec(sys.argv[2])
except Exception:
    spec = None
paths = list(getattr(spec, "submodule_search_locations", None) or [])
print("installed" if any(os.path.isdir(os.path.join(p, "data")) for p in paths) else "orphan")
' "$DIST" "datadog_checks.$CHECK" 2>/dev/null)" || DIST_STATE="unknown"

case "$DIST_STATE" in
  orphan)
    echo "$DIST is recorded as installed but its files are gone -- clearing the metadata"
    sudo -u "$DD_AGENT_USER" "$DD_EMBEDDED_PYTHON" -m pip uninstall -y "$DIST" \
      || die "could not clear the stale $DIST metadata with $DD_EMBEDDED_PYTHON -- the install below would be skipped by pip and then fail on the missing package directory"
    ;;
  installed) echo "$DIST is installed and intact" ;;
  absent)    echo "$DIST is not installed; nothing to clear" ;;
  *)         echo "could not read $DD_EMBEDDED_PYTHON -- skipping the staleness check" ;;
esac

# --- Install ---------------------------------------------------------------
step "Installing into the Agent's embedded Python (as $DD_AGENT_USER)"
sudo -u "$DD_AGENT_USER" "$AGENT_BIN" integration install -w "$STAGED_WHEEL"

# --- Configure -------------------------------------------------------------
# The install created $CONFD_DIR and copied conf.yaml.example into it, owned by
# the invoking user (dd-agent, here). Activate it without clobbering an existing
# conf.yaml. Probe via sudo because the directory is usually dd-agent-only.
CONFD_DIR="$DD_CONFD/$CHECK.d"
step "Staging configuration in $CONFD_DIR"
if sudo -u "$DD_AGENT_USER" test -f "$CONFD_DIR/conf.yaml"; then
  echo "existing conf.yaml left untouched"
elif sudo -u "$DD_AGENT_USER" test -f "$CONFD_DIR/conf.yaml.example"; then
  sudo -u "$DD_AGENT_USER" cp "$CONFD_DIR/conf.yaml.example" "$CONFD_DIR/conf.yaml"
  sudo -u "$DD_AGENT_USER" chmod 640 "$CONFD_DIR/conf.yaml"
  echo "created conf.yaml from the packaged example"
else
  # ponytail: the wheel ships data/conf.yaml.example, so this is a "should never
  # happen" guard — warn instead of failing so the install itself isn't lost.
  echo "warning: no conf.yaml or conf.yaml.example in $CONFD_DIR — create conf.yaml yourself"
fi

# --- Next steps (these are yours; the last one needs root) -----------------
cat <<EOF

Done. The check is installed but not yet reporting. Next:

  1. Set stats_url (and any tags) in the config:
       sudo -u $DD_AGENT_USER \${EDITOR:-vi} $CONFD_DIR/conf.yaml
  2. Verify — runs the check once, as the Agent user:
       sudo -u $DD_AGENT_USER $AGENT_BIN check $CHECK
  3. Restart the Agent to begin reporting (needs root):
       sudo systemctl restart datadog-agent
EOF
