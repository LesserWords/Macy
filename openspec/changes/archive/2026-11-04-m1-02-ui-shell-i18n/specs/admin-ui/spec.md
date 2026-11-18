## ADDED Requirements

### Requirement: Translation catalog completeness gate
The system SHALL verify via automated CI checks that all user-facing UI message strings are fully translated in both `pt_BR` and `en_US` message catalogues with zero fuzzy entries.

#### Scenario: Untranslated string fails CI gate
- **WHEN** a message catalog contains untranslated or fuzzy strings
- **THEN** the translation check script exits with a non-zero status

### Requirement: Locale-aware date and currency formatting
The system SHALL format dates and monetary amounts according to the active UI locale while retaining USD currency units.

#### Scenario: Format dates and currency for pt-BR and en-US
- **WHEN** displaying dates and currency in pt-BR
- **THEN** dates use DD/MM/YYYY and monetary amounts use comma decimal separators
- **WHEN** displaying dates and currency in en-US
- **THEN** dates use MM/DD/YYYY and monetary amounts use period decimal separators
