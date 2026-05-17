# Research Recommendations

These recommendations were pulled from patterns in mature network automation,
parsing, compliance, and change-validation tools.

## Implemented Recommendations

1. Keep Flask routes thin and move app logic into a service layer.
2. Add JSON API endpoints for automation and WSL/curl workflows.
3. Redact secrets by default in generated output and API responses.
4. Produce machine-readable migration reports with counts and validation results.
5. Categorize warnings/review lines by operational domain.
6. Keep batch conversion exportable as a zip bundle.
7. Add project zip exports with optional source-config inclusion.
8. Add container support for repeatable WSL/Linux runs.
9. Add CI to run tests and compile checks on every push.
10. Keep platform/target/profile registries explicit so new vendors can be added safely.

## References

- Batfish: https://github.com/batfish/batfish
- CiscoConfParse2: https://github.com/mpenning/ciscoconfparse2
- Nornir: https://github.com/nornir-automation/nornir
- NAPALM configuration workflow: https://napalm.readthedocs.io/en/latest/tutorials/changing_the_config.html
- Junos PyEZ commit/check workflow: https://www.juniper.net/documentation/us/en/software/junos-pyez/junos-pyez-developer/topics/task/junos-pyez-program-configuration-committing.html
