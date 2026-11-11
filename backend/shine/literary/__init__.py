"""The V2.1 elastic literary engine.

One content scheduler, one five-dimension fact store and one call governor on
top of the existing four-stage pipeline.  The package never replaces the
four-stage product contract (import -> outline -> script -> storyboard); it
makes each stage resumable, budgeted and locally recoverable.

Design invariants carried by every module here:

* literary text first, machine structure second - a complete answer is saved as
  a candidate draft even when its optional JSON fields are wrong;
* one severity matrix decides what may block what, and a hard block is always
  scoped to named units and carries a next action;
* the five dimensions are five *views* of one fact ledger, never five serial
  model calls;
* every physical provider request is budgeted from the exact bytes that are
  sent, and the attempt is recorded before it leaves the machine.
"""

from __future__ import annotations

__all__ = ["triage", "salvage", "governor", "memory"]
