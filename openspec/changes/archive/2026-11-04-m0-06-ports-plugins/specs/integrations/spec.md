## MODIFIED Requirements

### Requirement: Plugin settings and encrypted secrets management
The system SHALL configure plugins per port, validate settings via JSON schema, and store secrets using Fernet encryption.

#### Scenario: Secrets are encrypted and never exposed
- **WHEN** integration credentials are saved
- **THEN** secrets are encrypted using Fernet and never logged or exposed in responses

#### Scenario: Missing required integration blocks pipeline run
- **WHEN** a pipeline stage requires an unconfigured port
- **THEN** the run becomes blocked with an integration missing status naming the port and an audit event is emitted

## ADDED Requirements

### Requirement: Port protocols and entry-point plugin discovery
The system SHALL define 7 typing.Protocol ports and discover plugins dynamically at runtime through standard Python entry points.

#### Scenario: Discover registered port plugins
- **WHEN** plugin registry discovers plugins for a port
- **THEN** registered plugins matching the port protocol are returned

#### Scenario: Fakes pass contract suites
- **WHEN** contract test suites execute against in-memory fake adapters
- **THEN** all contract assertions pass for each of the 7 ports

#### Scenario: MultiFernet key rotation preserves secret readability
- **WHEN** an encryption key rotation is executed with rotate-key
- **THEN** secrets are re-encrypted under the new primary key and remain decryptable
