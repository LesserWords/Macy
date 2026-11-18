## 1. Port protocols, registry, and fakes

- [x] 1.1 Write failing tests in `tests/test_integrations.py` asserting discovery of 7 ports, `IntegrationMissing(port)`, and `cli plugins list` → verify: tests fail
- [x] 1.2 Implement `engine/integrations/ports.py`, `engine/integrations/registry.py`, `engine/integrations/fakes.py`, and register fakes in `pyproject.toml` entry points → verify: tests pass

## 2. Contract test suites

- [x] 2.1 Write shared contract suite harness in `tests/test_contract_suites.py` parametrised across all registered plugins and fakes for the 7 ports → verify: contract suites pass on all 7 fake adapters

## 3. Per-workspace integration configuration and Fernet encryption

- [x] 3.1 Write failing tests in `tests/test_integrations.py` asserting `plugin_config` encryption/decryption, secret masking, missing integration error, and `rotate-key` → verify: tests fail
- [x] 3.2 Implement `engine/integrations/config.py` with Fernet / MultiFernet encryption and `cli.py` commands (`plugins list`, `integrations set`, `rotate-key`) → verify: all tests pass
