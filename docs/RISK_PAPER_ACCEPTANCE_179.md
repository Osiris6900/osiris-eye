# Risk and paper execution acceptance (#179)

This is an executable acceptance specification, not a passing test result.

## Required deterministic scenarios

| Scenario | Expected behavior |
| --- | --- |
| Position limit exceeded | Reject proposal; zero paper fills |
| Portfolio exposure cap exceeded | Reject proposal; zero paper fills |
| Duplicate order id after restart | No second fill; preserve original audit record |
| Kill switch enabled | Reject all new proposals; no paper fills |
| Missing or stale market observation | Fail closed before risk approval |
| Risk service unavailable | Fail closed; no implicit approval |
| Accepted synthetic proposal | One paper-only fill, traceable to risk decision |

## Evidence required for completion

- Run tests against the actual Python risk and paper workflow interfaces.
- Attach exact commit SHA, test commands, CI links and sanitized artifacts.
- Verify that rejected proposals never reach execution, including after restart.
- Keep live broker order routing disabled. A checklist is not release evidence.

Tracking issue: https://github.com/Osiris6900/osiris-eye/issues/179
