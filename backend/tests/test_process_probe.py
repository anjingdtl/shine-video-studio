"""Read-only process probes that classify a PID the OS will not describe.

A Medium integrity caller cannot open a SYSTEM process, so the authoritative
identity check is unavailable for a PID that Windows recycled to a service
host. The probe exists so a stale process record can be told apart from a
running process instead of blocking the launcher forever.
"""

from __future__ import annotations

import os

import pytest

from shine.comfyops.process_probe import (
    GONE,
    RECYCLED,
    SAME,
    UNKNOWN,
    _stamp_from_iso,
    classify_unverifiable,
    creation_stamps,
)


def test_stamp_conversion_keeps_the_exact_filetime():
    # 2026-11-02T00:00:00Z expressed as a Windows creation stamp.
    assert _stamp_from_iso("2026-11-02T00:00:00.0000000Z") == 134380512000000000


def test_stamp_conversion_accepts_a_local_offset():
    assert _stamp_from_iso("2026-11-02T08:00:00.0000000+08:00") == 134380512000000000


def test_stamp_conversion_rejects_garbage():
    assert _stamp_from_iso("not a timestamp") is None


@pytest.mark.parametrize(
    "pid,birth,stamps,expected",
    [
        # The PID still holds the recorded process.
        (10, "134380512000000000", {10: 134380512000000000}, SAME),
        # The WMI stamp is reported in microseconds, so the same process is
        # still slightly apart from the authoritative stamp.
        (10, "134380512000000000", {10: 134380512000000000 + 3}, SAME),
        (10, "134380512000000000", {10: 134380512000000000 + 9_999}, SAME),
        # The PID now belongs to a process created far later.
        (10, "134380512000000000", {10: 134380512000000000 + 20_000_000}, RECYCLED),
        # The PID is absent from a readable process table.
        (10, "134380512000000000", {11: 134380512000000000}, GONE),
        # An unreadable process table is unknown, never death.
        (10, "134380512000000000", {}, UNKNOWN),
        # A record without a birth cannot be compared at all.
        (10, None, {10: 134380512000000000}, UNKNOWN),
        (10, "not-a-number", {10: 134380512000000000}, UNKNOWN),
    ],
)
def test_classification_distinguishes_recycled_from_running(pid, birth, stamps, expected):
    assert classify_unverifiable(pid, birth, stamps) == expected


def test_a_recycled_pid_is_far_outside_the_tolerance():
    # A PID cannot be handed to a different process in the same instant, so a
    # recycled number must never be mistaken for the recorded one.
    stamps = {10: 134380512000000000}
    assert classify_unverifiable(10, "134380512000000000", stamps) == SAME
    assert classify_unverifiable(10, "134380512000000001", stamps) == SAME
    assert classify_unverifiable(10, "134380512999999999", stamps) == RECYCLED


@pytest.mark.skipif(os.name != "nt", reason="Windows process table")
def test_probe_reads_the_running_process_creation_stamp():
    from shine.comfyops.installer import process_identity

    stamps = creation_stamps()
    if not stamps:
        pytest.skip("the process table could not be read on this machine")
    direct = process_identity(os.getpid())
    assert direct is not None
    # The WMI stamp is reported in microseconds and the authoritative stamp in
    # 100 ns units, so the same process differs by less than one microsecond.
    # An exact comparison would be wrong, which is why the classifier uses a
    # tolerance instead.
    assert abs(stamps[os.getpid()] - int(direct)) <= 10
