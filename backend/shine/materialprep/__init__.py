"""Director-side AI material preparation.

A material-preparation plan shares the ``preproduction_plans`` table with the
production contract but carries ``purpose='asset_preparation'``; every
production selector filters on purpose so a material revision can never hijack
the frozen H3 manifest.  See ``docs/adr/0006-director-ai-material-preparation.md``.
"""
