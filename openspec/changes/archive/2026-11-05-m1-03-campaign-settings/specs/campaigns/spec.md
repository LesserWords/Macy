## ADDED Requirements

### Requirement: Active plugin locale support check
The system SHALL verify that every active plugin configured for the workspace supports the chosen campaign locale before saving campaign settings, and SHALL reject unsupported locales with an `UnsupportedLocale` error naming the offending plugin.

#### Scenario: Unsupported locale rejected naming plugin
- **WHEN** an owner attempts to save a campaign locale that is not supported by an active plugin
- **THEN** saving fails with an `UnsupportedLocale` error whose message includes the plugin name

### Requirement: Campaign run snapshot isolation
The system SHALL ensure that updating campaign settings takes effect on subsequent runs while any run already in progress retains its snapshotted settings.

#### Scenario: In-progress run unaffected by campaign update
- **WHEN** campaign settings (such as `run_cap_usd` or `articles_per_week`) are updated while a run is already in progress
- **THEN** the in-progress run retains its original `run_cap_usd` snapshot and the next created run uses the newly saved values

### Requirement: Pre-save monthly cost estimation
The system SHALL calculate and display the estimated monthly cost (`articles_per_week * per_article_estimate + fixed_research`) on the Campaign Settings screen and update it dynamically when the weekly volume changes prior to saving.

#### Scenario: Changing weekly volume updates estimated monthly cost before save
- **WHEN** the owner changes the `articles_per_week` volume on the Campaign Settings screen
- **THEN** the displayed estimated monthly cost updates immediately before the campaign is saved
