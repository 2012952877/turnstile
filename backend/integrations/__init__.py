"""Adapters for systems this project does not own.

`gateway` talks to model providers, `ledger` to Azure Table Storage, `reconciliation` to
Log Analytics. Each one converts a foreign shape into a `domain` type, so the layers above
never handle a vendor payload directly.

May import `domain`, `persistence` and `config`. Must not import `services` or `api`.
"""
