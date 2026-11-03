# Autonomous P2P milestone agent

The `codex-next-p2p.yml` workflow starts an OpenAI Codex coding run after a P2P pull request is merged into `main`.

## One-time setup

1. Create an OpenAI API key at https://platform.openai.com/api-keys.
2. In this GitHub repository, open **Settings → Secrets and variables → Actions**.
3. Choose **New repository secret**.
4. Set the name to `OPENAI_API_KEY` and paste the key as its value.
5. Save the secret. Never commit the key or paste it into an issue, PR, or chat.

The workflow reads the key only from `${{ secrets.OPENAI_API_KEY }}`. If the secret is absent or invalid, the Codex job will fail safely and will not create a code PR.

## What happens after a P2P PR is merged

- GitHub Actions checks that the merged PR targets `main` and its title contains `P2P`.
- Codex inspects the current P2P implementation and chooses one non-duplicate, scoped milestone.
- Codex edits the workspace under a restricted workspace permission profile.
- Rust formatting and tests run in the workflow.
- If there are changes, GitHub opens a PR from `codex/next-p2p-milestone`.
- The new PR must pass the repository's normal CI checks. This workflow does not merge the generated PR.

The agent is deliberately not given permission to merge PRs. Keep repository branch protection and required status checks enabled. Review the generated diff and workflow logs, especially when changing networking, identity, or trust behavior.

## Scope and limitations

This is an autonomous coding workflow, not a continuously running ChatGPT conversation. It can create a proposed PR after a merge event, but it cannot send messages into an existing chat session. Tests that run on localhost do not prove public Internet reachability.
