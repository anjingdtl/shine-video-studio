"""Long-form production: the run/episode/batch layer over the existing series.

Nothing in this package is a second scheduler.  ``advance`` shortens one run
step into a bounded transaction that only decides which batch may become
claimable; the resident Worker still claims exactly one ``ProductionSeries``
and still owns the GPU lease, the renderer and every job below it.
"""
