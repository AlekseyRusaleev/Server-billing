# Contributing

Thank you for considering a contribution to Server Billing Manager.

This is a **best-effort** open-source project: pull requests with clear scope are welcome; issue responses are not guaranteed.

## What helps most

1. **Add a hosting provider** to the catalog (templates, plans, optional API connector).
2. **Fix bugs** with a minimal reproduction and a focused patch.
3. **Improve docs** (README, SECURITY.md, translations).

## Adding a provider

1. Copy `scripts/provider_entry.example.json` as a reference.
2. Update:
   - `app/provider_templates.json` — name, URLs, `integration_type`, `referral_url`, `api_docs_url`
   - `app/provider_plans.json` — indicative plans and `prices_as_of`
   - `app/provider_locations.json` — countries/locations
3. If the provider has a read-only API, add a connector in `app/integrations.py` and register it in `app/connectors.py`.
4. Mark the template with badge **API sync** in the catalog when sync works.

See the Russian README section [Каталог провайдеров](README.md#каталог-провайдеров) for the full workflow.

## Pull requests

- One logical change per PR (one provider or one bug fix).
- Do not commit `.env`, `secrets/`, or `data/`.
- Match existing code style; avoid unrelated refactors.

## Security issues

Do not open public issues for vulnerabilities. Contact the repository owner privately (GitHub profile or email listed on the repo).

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).
