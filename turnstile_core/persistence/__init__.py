"""Where usage and configuration are stored, and how they are read back.

`repository` and `in_memory` are stable facades for the PostgreSQL and explicit demo/test
implementations. Their responsibility-scoped modules implement the shared
`repository_contract.QueryRepository`; `factory` picks the facade from settings.

May import `domain` and `config`. Must not import `services`, `integrations` or `api`.
"""
