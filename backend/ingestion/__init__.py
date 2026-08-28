"""Turning observed traffic into stored usage rows.

`processor` handles the Event Hub events APIM emits; `copilot_cli_usage` reads the GitHub
Copilot CLI's own local ledger. Both end at `persistence`, and neither is reachable from
the HTTP API -- they are driven by the Function app and a CLI respectively.

May import `domain`, `persistence` and `config`.
"""
