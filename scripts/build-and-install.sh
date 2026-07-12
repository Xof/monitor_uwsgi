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
#
# Overridable via environment:
#   PYTHON          python used to build the wheel   (default: python3)
#   DD_AGENT_USER   the Datadog Agent user           (default: dd-agent)
#   DD_CONFD        Agent conf.d directory           (default: /etc/datadog-agent/conf.d)
#   DATADOG_AGENT   path to the datadog-agent binary (default: found on PATH)
#
set -euo pipefail

PYTHON="${PYTHON:-python3}"
DD_AGENT_USER="${DD_AGENT_USER:-dd-agent}"
DD_CONFD="${DD_CONFD:-/etc/datadog-agent/conf.d}"
CHECK="uwsgi_stats"

usage() {
  sed -n '2,29p' "$0" | sed 's/^# \{0,1\}//'
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
WHEEL="$(ls -t dist/datadog_uwsgi_stats-*.whl 2>/dev/null | head -1 || true)"
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
