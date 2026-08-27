---
id: 0005
title: Treat the re-raise on a stats-read failure as a public behavioral contract
date: 2026-08-27
status: Accepted
summary: Commit to check() re-raising after submitting can_connect CRITICAL, because the Agent collector's [ERROR] instance marker is the only machine-readable signal a scrape failed and an out-of-repo deploy consumer keys on it.
---

# 0005. Treat the re-raise on a stats-read failure as a public behavioral contract

## Context

When `read_stats` fails, `UwsgiStatsCheck.check()` submits `uwsgi.can_connect`
CRITICAL and then re-raises. The behaviour has been there since the initial
implementation (ADR 0001) and was never argued for; it read as an ordinary
choice about error propagation, which is precisely the problem this record
fixes.

It is not an ordinary choice. The Datadog Agent collector marks an instance
`[ERROR]` only when `check()` raises, and that marker is the only
machine-readable signal that a scrape failed:

- `datadog-agent check <name>` **exits 0 whether the check succeeded or
  errored**. Measured on the same binary for the stock `mcache` integration:
  an instance in `[ERROR]` exits 0, and so does a healthy one. The exit status
  answers "did the check RUN", not "did it SUCCEED". (A check the Agent cannot
  *load* is different, and does exit non-zero — but a loaded check whose stats
  socket refuses the connection is an instance error, so it comes back rc 0.)
- The service-check statuses this check submits are not a substitute, because
  they are not distinguishable in that output by severity alone: an
  unreachable stats server reports `can_connect` CRITICAL, but a site that is
  merely saturated and answering perfectly reports `worker_saturation`
  CRITICAL. Keying on "CRITICAL appears" cannot tell a dead vassal from a busy
  one.

An out-of-repo consumer depends on this. The specifics that follow were read
from the `datadog_agent` ansible role in the (private) peep deployment
repository on 2026-08-27, while writing this record: they describe that role
as it stood then, and nothing in this repository can verify them or notice if
they change. The role runs `datadog-agent check uwsgi_stats` after installing
the integration and computes its verdict as `rc == 0 and '[ERROR]' not in
stdout`. On a host whose uWSGI vassal ini already exists — i.e. a converged
host that is meant to be serving — a non-clean verdict fails the play rather
than converging the host. (A host being built for the first time has no
vassal ini yet and gets a report instead of a refusal, because the vassal is
deployed one step later.)

The decision below does not rest on those specifics. It needs only the
weaker claim, which is the one this repository can act on: some consumer
outside this repository distinguishes a failed scrape from a healthy one by
the `[ERROR]` marker, and therefore by whether `check()` raised.

Two properties make this coupling sharp rather than theoretical. The role
clones this repository at **branch head** by design — deliberately not
SHA-pinned, so fixes to the check ship without editing the playbook — which
means a change here reaches production hosts without any deliberate uptake
step. And the failure is silent in both directions: no test in this
repository asserts anything about downstream consumers, and no test in peep
can observe this repository's source.

The realistic way this breaks is not malice or carelessness but good taste.
The common Datadog-integration idiom is catch → submit CRITICAL → `return`,
and it looks strictly tidier than raising: the check "handles" its own error
and reports it through the service-check channel built for exactly that. A
reviewer with no knowledge of the downstream consumer would be right to
suggest it. Applied here it yields instance `[OK]`, rc 0, and no marker — so
a refused stats socket reads as healthy and a dead vassal converges green
fleet-wide.

## Decision

We will treat the `raise` in `check()`'s broad `except` handler as a public
behavioral contract of this integration, on the same footing as its metric
names and service-check names: a stats read that fails submits
`uwsgi.can_connect` CRITICAL **and propagates the exception**, so that the
Agent marks the instance `[ERROR]`.

Changing it is a breaking change to be made deliberately and coordinated with
consumers, not a refactor.

No code changes: the behaviour was already correct. What changes is that the
contract is now stated in the four places a reader can arrive from — a
comment on the `raise` itself, the invariants list in `ARCHITECTURE.md`, this
record, and a docstring on
`tests/test_check.py::test_can_connect_critical_and_reraise_on_failure`,
which is the executable guard.

## Alternatives considered

- **Leave it undocumented and rely on the existing test.** The test does fail
  if `raise` becomes `return`, so the behaviour is guarded. But a bare failing
  assertion communicates "this test expected an exception", not "an
  unrelated repository refuses to deploy without it" — and the natural
  response to a test that blocks a tidier implementation is to update the
  test. A guard that does not explain itself invites its own removal.
- **Add the comment only, with no ADR or invariant entry.** Rejected as
  placing the whole explanation at one point of contact. Someone auditing
  error handling repo-wide, or reading `ARCHITECTURE.md` to pick the codebase
  up cold, would not see it.
- **Have the consumer key on `CRITICAL` in the check output instead, removing
  the coupling to the raise.** Rejected on correctness, not on layering:
  `worker_saturation` reports CRITICAL for a saturated, healthy site, so this
  refuses to converge hosts that are merely busy. The `[ERROR]` marker is
  specifically "this instance raised", which is the condition worth refusing
  on.
- **Have the consumer key on the exit status.** Not available; it is 0 for an
  instance error, which is the measurement that forced the marker-based
  approach in the first place.
- **Expose a dedicated machine-readable health surface for deploy tooling
  (a JSON verdict, a separate CLI entry point).** Rejected as
  disproportionate. It would be new public surface, with its own
  compatibility obligations, to replace a signal the Agent already emits
  correctly. Revisit only if a second consumer appears with needs the
  `[ERROR]` marker cannot meet.
- **Pin the consumer to a SHA so the coupling is a snapshot rather than
  live.** Rejected, and not ours to decide: branch-head tracking is a
  deliberate choice in the peep role so that fixes to this check ship without
  a playbook edit. Pinning would trade this hazard for stale checks on
  production hosts.

## Consequences

- The re-raise is now covered by comment, invariant, record, and test. A
  future refactor toward the catch-and-return idiom meets an explanation at
  whichever of the four it encounters first.
- This integration has an acknowledged consumer outside its own repository,
  and its observable failure behaviour is now part of its interface. That is
  a real constraint: it narrows what can be changed in `check()`'s error path
  without coordination, and it will not show up in this repository's CI.
- The contract is expressed in terms of Agent behaviour we do not control.
  If a future Agent release changes when an instance is marked `[ERROR]`, or
  starts propagating instance errors into the exit status, this record's
  reasoning needs re-examination even though this repository's code has not
  changed.
- The reference to a private repository is deliberate. It is less useful to a
  reader outside the project than a self-contained rationale would be, but
  naming the actual consumer is what makes the constraint checkable rather
  than folklore. The mechanism (`[ERROR]` marker, rc 0 for instance errors) is
  stated in full here, so the record stands on its own without access to peep.
- Not addressed: nothing verifies the coupling end to end. The guarantee is
  four pieces of prose and one unit test on this side, and a role comment on
  the other. An integration test spanning both repositories would be the real
  fix and does not exist.
