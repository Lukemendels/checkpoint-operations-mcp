"""Build the synthetic PIMS/PMIS SQLite seed the server reads by default.

    uv run python scripts/build_pims_seed.py            # refuses to overwrite
    uv run python scripts/build_pims_seed.py --replace  # rebuild

Defaults reproduce the seed the evaluation was run against: 2026-10-18 for 30
days, random seed 20261117, written to data/synthetic/pims/. All other options
of `example_server.pims.generator` are accepted (see --help).
"""

from __future__ import annotations

from example_server.pims.generator import main

if __name__ == "__main__":
    main()
