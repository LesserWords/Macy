## 1. Dockerfile and container image

- [x] 1.1 Write failing test `tests/test_compose_and_docker.py` validating Dockerfile exists, uses multi-stage uv build, non-root user, and exposes the app entrypoint → verify: `uv run pytest tests/test_compose_and_docker.py` fails on missing Dockerfile (`test_compose_and_docker.py::test_dockerfile_structure_and_security`)
- [x] 1.2 Implement `Dockerfile` and `.dockerignore` → verify: `docker build -t content-engine:test .` succeeds and `docker run --rm content-engine:test python -c "import engine"` exits 0

## 2. Compose configurations (base, dev, e2e, prod)

- [x] 2.1 Write failing test in `tests/test_compose_and_docker.py` asserting all 4 compose files exist, define required services (`db`, `migrate`, `app`, `worker`), and validate via `docker compose config` → verify: `uv run pytest` fails on missing compose files (`test_compose_and_docker.py::test_compose_files_exist_and_define_services`)
- [x] 2.2 Implement `compose.yaml`, `compose.override.yaml`, `compose.e2e.yaml`, `compose.prod.yaml`, and `db/migrations/20261103000000_init.sql` → verify: `docker compose config` succeeds for all environments, and `docker compose up -d` brings services healthy

## 3. Squawk migration linter and CI compose-config job

- [x] 3.1 Write failing test in `tests/test_compose_and_docker.py` asserting squawk is configured in `scripts/check.sh` and `.pre-commit-config.yaml` → verify: pytest fails on missing squawk invocation (`test_compose_and_docker.py::test_squawk_and_compose_config_configured`)
- [x] 3.2 Wire squawk into `scripts/check.sh`, `.pre-commit-config.yaml`, and add CI jobs `compose-config` and `squawk` in `.github/workflows/ci.yml` → verify: test passes and CI workflows validate
- [x] 3.3 Verify squawk flags a dangerous migration (plain `CREATE INDEX` on an existing table) and exits non-zero → verify: squawk reports `require-concurrent-index-creation`, then probe is deleted

## 4. Documentation and dev workflow

- [x] 4.1 Update `README.md` and ADR action items documenting `docker compose up` as the single dev setup command → verify: clean `scripts/check.sh` and `openspec validate`
