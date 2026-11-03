# Autonomous P2P milestone agent

The `codex-next-p2p.yml` workflow starts an OpenAI Codex coding run only after the repository's `CI` workflow completes successfully on `main` for a commit whose message contains `P2P`. It checks out the exact commit that passed CI.

## One-time setup

1. Create an OpenAI API key at https://platform.openai.com/api-keys.
2. In this GitHub repository, open **Settings → Secrets and variables → Actions**.
3. Choose **New repository secret**.
4. Set the name to `OPENAI_API_KEY` and paste the key as its value.
5. Save the secret. Never commit the key or paste it into an issue, PR, or chat.

The workflow reads the key only from `${{ secrets.OPENAI_API_KEY }}`. If the secret is absent, invalid, or the API project has no available quota, the Codex job will fail safely and will not create a code PR.

## What happens after a P2P change lands on main

- GitHub Actions runs the normal `CI` workflow on `main`.
- Only after that run succeeds, the Codex workflow checks that the tested commit message contains `P2P`.
- Codex inspects the exact tested P2P implementation and chooses one non-duplicate, scoped milestone.
- Codex edits the workspace under a restricted workspace permission profile.
- Rust formatting and tests run again in the Codex workflow.
- If there are changes, GitHub opens a PR from `codex/next-p2p-milestone`.
- The new PR must pass the repository's normal CI checks. The workflow does not merge its generated PR.

The agent is deliberately not given permission to merge PRs. Keep repository branch protection and required status checks enabled. Review the generated diff and workflow logs, especially when changing networking, identity, or trust behavior.

## Scope and limitations

This is an event-triggered coding workflow, not a continuously running ChatGPT conversation. GitHub Actions reacts to successful CI events, but it cannot send messages into an existing chat session. API usage quota and billing must remain available for Codex runs. Tests that run on localhost do not prove public Internet reachability.
