# P2P milestone workflow

P2P development is currently maintained through human-directed changes and normal pull requests. The repository does not run an API-dependent Codex coding workflow.

## Development loop

1. Inspect the current Rust/libp2p implementation and existing process-level acceptance tests.
2. Select one scoped milestone that does not duplicate existing behavior.
3. Implement the change with focused Rust tests and, where useful, a multi-process acceptance test.
4. Update the relevant operational documentation.
5. Run formatting, unit tests, and P2P process-level tests.
6. Open a pull request and merge only after all required GitHub Actions checks pass.

## API and credentials

No OpenAI API key is required for this manual workflow. Do not add API keys or other credentials to the repository, workflow files, issues, or pull requests. If API-driven automation is reconsidered later, it must be explicitly reintroduced with a separately reviewed workflow and correctly configured API billing.

## Network validation limits

Localhost and same-host multi-process tests prove only the behavior exercised in that test environment. They do not prove public Internet reachability, cross-provider connectivity, NAT traversal, or production availability. A public launch still requires independently hosted nodes and a cross-host acceptance test.
