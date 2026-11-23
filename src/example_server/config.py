"""Server configuration loaded from environment variables or a .env file.

Every field can be overridden by an environment variable of the same name
(upper- or lower-case). Copy `.env.example` to `.env` for local development.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """MCP server settings. Override any field via environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Transport ─────────────────────────────────────────────────────────────
    # "stdio"           — local subprocess (Claude Desktop / Claude Code / Zed)
    # "streamable-http" — remote HTTP server (containers, cloud deploys)
    #
    # NOTE: app.py also auto-selects HTTP when a platform injects DATABRICKS_APP_PORT
    # or PORT, so you usually do NOT need to set this on a cloud host.
    mcp_transport: str = "stdio"
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 8000

    # ── Dataset API keys ──────────────────────────────────────────────────────
    # Add a typed field for each API key your tools need, then read it from
    # `settings.<field>` in your tool. Document the matching env var in
    # `.env.example`. Example:
    #
    #   example_api_key: str = ""   # ← set EXAMPLE_API_KEY in the environment

    # ── Staffing tools ────────────────────────────────────────────────────────
    # Directory holding the synthetic staffing tables (see
    # data/synthetic/staffing/README.md). Empty → the committed fixture.
    staffing_data_dir: str = ""  # ← set STAFFING_DATA_DIR to point elsewhere
    # "pims_seed" (default): staffing tools read the synthetic PIMS SQLite seed
    # (PIMS_DB_PATH) plus data/synthetic/pims_staffing/. "csv_fixture": read
    # the CSV fixture in STAFFING_DATA_DIR instead.
    staffing_source: str = "pims_seed"  # ← STAFFING_SOURCE


settings = Settings()
