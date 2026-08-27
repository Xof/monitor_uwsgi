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

Deploy tooling outside this repository depends on this. It runs
`datadog-agent check uwsgi_stats` on a host after installing the integration,
reads the `[ERROR]` marker out of that output, and refuses to complete a
deployment when a host that is supposed to be serving reports one. That is
the whole of the coupling this record is about, and it is stated at the level
of detail a reader of this repository can act on: the consumer's own
configuration is deliberately not described here, because it is not ours to
publish and because a description of it would go stale silently.

Two properties make the coupling sharp rather than theoretical. The consumer
tracks this repository at **branch head** rather than a pinned commit, by a
deliberate choice on its side, so that fixes to the check reach hosts without
an uptake step there — which means a change here reaches production without
one either. And the failure is silent in both directions: no test in this
repository asserts anything about downstream consumers, and no test on the
other side can observe this repository's source.

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
- **Ask the consumer to pin a SHA, so the coupling is a snapshot rather than
  live.** Rejected, and not ours to decide in any case: branch-head tracking
  is a deliberate choice on the consumer's side, so that fixes to this check
  ship without an edit there. Pinning would trade this hazard for stale
  checks on production hosts.

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
- The consumer is described by what it does, not by name. This repository is
  public and the consumer's is not, so its identity and configuration are not
  ours to publish here. The cost is real and worth stating: a reader cannot
  go and check the claim, which makes this the one part of the record that
  has to be taken on trust. What keeps it from being folklore is that the
  mechanism it depends on — the `[ERROR]` marker, and rc 0 for an instance
  error — is Agent behaviour, stated in full above, and verifiable by anyone
  against any integration.
- Because the consumer is unnamed here, this record cannot be the thing that
  tells a future maintainer *who* to coordinate with before changing
  `check()`'s error path. That has to come from elsewhere.
- Not addressed: nothing verifies the coupling end to end. The guarantee is
  four pieces of prose and one unit test on this side, and a comment on the
  other. An integration test spanning both repositories would be the real fix
  and does not exist.
