# Security Policy

## Supported Versions

Security updates are applied to the latest release on the default branch.

## Reporting a Vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities, credential leaks, SSRF bypasses, or tenant-isolation defects.

Instead, report vulnerabilities privately through GitHub Security Advisories (**Security -> Advisories -> Report a vulnerability**) on this repository. Include:

- A clear description of the issue and affected component (`engine/safe_http.py`, `engine/mcp/`, `engine/auth.py`, `engine/db.py`, etc.).
- Steps or a minimal test case to reproduce the behavior.
- Potential impact (e.g., cross-tenant access, SSRF bypass, secret exposure).

## Security Architecture Highlights

- **Outbound HTTP / SSRF Guard (`engine/safe_http.py`):** Blocks loopback, private, link-local, and cloud metadata IP ranges across research fetching, sitemaps, URL audits, and webhook deliveries.
- **Tenant Isolation (`engine/db.py`):** All tenant tables carry `workspace_id NOT NULL` with composite foreign keys and parameterized queries.
- **Secrets at Rest:** Integration credentials and webhook signing secrets are encrypted using Fernet (`APP_ENCRYPTION_KEY`) and scrubbed from logs and Sentry telemetry.
- **Fail-Closed Auth:** Production environments refuse fake test identities (`MACY_TEST_USER_EMAIL`), mock stores (`--mock`), and missing MCP Bearer tokens.
