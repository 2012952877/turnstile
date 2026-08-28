# Security policy

## Reporting

Report suspected vulnerabilities privately to the repository owner. Do not open a public issue containing credentials, tenant information, customer identifiers, request payloads, or exploit details.

## Supported version

The latest commit on `main` is the supported private development version.

## Handling requirements

- Never commit credentials, tokens, connection strings, database dumps, publish profiles, or populated deployment parameters.
- Rotate any value that may have been exposed before relying on history deletion.
- Use managed identity and least-privilege Azure roles where supported.
- Keep prompts and completions out of telemetry, logs, metrics, and database records.
- Review dependency alerts and security updates before merging.

See `docs/security.md` for the implementation model and release checklist.
