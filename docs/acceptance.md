# Graphiti Acceptance Harness

The acceptance harness automates the 14-day ingestion scenario described in the PRD. It feeds synthetic data through every poller, flushes the MCP logger, and verifies that the resulting episodes cover all required sources.

## Running the Harness in Tests

1. Ensure the Python dependencies are installed (no additional packages required beyond the repository).
2. Execute `pytest tests/test_acceptance_harness.py` to run the harness against the built-in fixture dataset.
3. The harness returns a metrics dictionary with counts for Gmail, Drive, Calendar, Slack, and MCP. All values should be non-zero.

## Custom Datasets

Use `graphiti.harness.build_fixture_dataset()` as a starting point and then mutate the returned `AcceptanceDataset` to reflect custom scenarios (e.g., add tombstone Drive changes or cancelled calendar events). Pass the dataset to `AcceptanceTestHarness.run()` alongside a recording episode store.

## Integrating with CI

- Include the acceptance harness test in the CI pipeline to validate ingestion logic before release.
- The harness does not require live network credentials; all clients are in-memory stubs.
- Any regression that prevents a poller from emitting episodes causes the test to fail, providing early feedback before manual verification.

