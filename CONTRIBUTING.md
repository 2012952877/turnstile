# Contributing

Turnstile is an open-source project released under the MIT License. Contributions are welcome through focused, reviewed pull requests.

## Development workflow

1. Create a focused branch from `main`.
2. Keep configuration and secrets outside Git.
3. Add or update tests with every behavior change.
4. Run the validation commands documented in `README.md`.
5. Open a pull request with the behavior change, risk, validation evidence, and deployment impact.

Do not edit an applied migration. This repository starts with `001_initial_schema`; add a new numbered migration for every future schema change.

Infrastructure changes require a reviewed `az deployment sub what-if` result. Cloud resource creation, deployment, and model onboarding require explicit environment approval.
