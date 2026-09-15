# External pinger setup

GitHub throttles `schedule` on free public repos hard — measured **9 runs in 28
hours** against a `*/15` cron (gaps up to 4h59m). The workflow itself is fine;
GitHub just won't call it often. So an external cron service calls
`workflow_dispatch` on a real 15-minute schedule instead.

The workflow's own `schedule:` stays in place as a free fallback if the pinger
ever dies.

## Step 1 — create a narrowly-scoped token (you must do this in the browser)

GitHub does not allow creating PATs via the API, so this part is manual.

1. https://github.com/settings/personal-access-tokens/new
2. **Token name:** `mbp-refurb-pinger`
3. **Expiration:** 1 year (put a calendar reminder — the pinger dies silently when it expires)
4. **Repository access:** *Only select repositories* → `mbp-refurb-monitor`
   — this is the important part. Do **not** pick "All repositories".
5. **Permissions:** Repository permissions → **Actions: Read and write**.
   Nothing else. Leave Contents at "No access".

> The two settings people get wrong, both silent until step 3:
> - leaving **Repository access** on *All repositories* (the default) — the token
>   then reaches every repo you own
> - choosing **Actions: Read-only** instead of *Read and write* — read-only can
>   list workflows just fine and only fails at the moment it tries to dispatch,
>   with `Resource not accessible by personal access token`
6. Generate, copy the `github_pat_...` value.

> Never use your `gh auth token` here. That one can reach every repo you own,
> and this token is going to live on someone else's server.

## Step 2 — verify the token is actually narrow

```bash
./scripts/verify-pinger-token.sh github_pat_xxxxx
```

Run this in your own terminal. Don't paste the token into a chat or an issue —
anywhere it lands is somewhere it has to be rotated from later.

It checks the token sees exactly **1** repo, **cannot** write file contents, and
**can** dispatch the workflow. If it reports more than 1 repo, re-scope it before
continuing — a leaked broad token is a much worse day than a missed MacBook deal.

## Step 3 — create the cron job

At https://cron-job.org (free account), create a job:

| Field | Value |
|---|---|
| **URL** | `https://api.github.com/repos/AmroAbujabal/mbp-refurb-monitor/actions/workflows/356287232/dispatches` |
| **Schedule** | Every 15 minutes |
| **Request method** | `POST` |
| **Request body** | `{"ref":"main"}` |

Custom headers (under Advanced):

```
Accept: application/vnd.github+json
Authorization: Bearer github_pat_xxxxx
Content-Type: application/json
X-GitHub-Api-Version: 2022-11-28
```

Enable the service's "notify on failure" email so a dead pinger is visible.

**Expect HTTP 204**, with an empty body — that is success for this endpoint. A
`401` means a bad/expired token, `403` means the token lacks Actions write, and
`404` usually also means insufficient permissions rather than a wrong URL.

## Step 4 — confirm it's actually firing

After ~40 minutes:

```bash
gh run list --limit 10 --json event,createdAt,conclusion \
  --jq '.[] | "\(.createdAt[11:16])UTC \(.event) \(.conclusion)"'
```

You want `workflow_dispatch` entries roughly 15 minutes apart. If they're absent,
check the job's execution history on cron-job.org — the response code there tells
you whether GitHub rejected the call.

## Failure modes to know about

- **The token expires** and the pinger silently stops. Nothing alerts you except
  the checks going quiet. The calendar reminder from Step 1 is the mitigation.
- **cron-job.org goes away or drops your job.** The workflow's own `*/15`
  schedule still fires every ~2h, so you degrade rather than go dark.
- The token grants Actions write, so whoever holds it can trigger (and cancel)
  workflow runs on this repo. It cannot read your code — the repo is public
  anyway — and it cannot touch any other repo.
