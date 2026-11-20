"""Unified platform foundation facades shared by director7 and manual4.

This package exposes single-authority read/write facades for:
* ``environment``: authoritative ComfyUI/Python/nodes/models/workflows facts,
  per-recipe capability verdict, Setup Worker preparation trigger, and shared
  isolated launch spec builder.
* ``runtime``: unified GPU lease arbitration, idle image-renderer retirement,
  and owned ComfyUI process-group lifecycle.
"""
