"""Synthetic PIMS/PMIS-shaped data layer (contributed as PIMS_PMIS_FastMCP v1.0).

- `schema`     Pydantic row models, table grains and the data dictionary
- `generator`  deterministic, offline synthetic seed builder (SQLite)
- `repository` bounded, parameterized, read-only queries and quality checks

All operational records are synthetic. Nothing here reads real PIMS or PMIS data.
"""
