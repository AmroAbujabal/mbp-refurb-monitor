# HANDOFF — MacBook Pro refurb monitor

_Last updated: 2026-09-11_

## Goal

Notify me on my phone when Apple Canada's refurbished store lists a **MacBook Pro
under $1900 CAD**. Check every 10–15 min, don't re-notify for the same listing,
log each check so I can tell it's alive.

## Current State

**Working and scheduled.** Running every 15 min via cron.

- Live run verified against apple.com: 87 MacBook Pros parsed, cheapest **$2,379**.
- **Nothing is under $1900 right now** — the cheapest MacBook Pro on the CA refurb
  store is a 14" M5 at $2,379. Expect `matches=0` and no pushes until the M1/M2
  stock comes back or prices drop. This is correct behaviour, not a broken monitor.
- ntfy topic: see `config.env` (chmod 600, gitignored). **Never copy it into a
  tracked file** — this repo is public. It is also stored as the `NTFY_TOPIC`
  GitHub Actions secret.
  **Subscribe on your phone before you rely on this** — see Next steps.

## Active Files

- `monitor.py` — everything (stdlib only)
- `config.env` — `NTFY_TOPIC`, `MAX_PRICE`
- `seen.json` — currently `{}` (no items under threshold)
- `monitor.log` — one line per run
- `test_monitor.py` — 12 self-checks
- `CLAUDE.md` — architecture + scheduling reference

## Changes Made

1. Reverse-engineered the page: products ship as JSON in
   `window.REFURB_GRID_BOOTSTRAP`, so no HTML scraping or browser is needed.
2. Parser walks the JSON tree for `title`+`partNumber`+`price` instead of a fixed
   path, and dedupes by part number.
3. Title normalization for non-breaking spaces/hyphens before matching
   `"macbook pro"` — the page also carries Air/iMac/Neo/Display listings.
4. Notify-once state in `seen.json`; re-notifies only on a *lower* price.
   State is written only after a successful push.
5. Structure changes (missing marker, unparseable JSON, zero products, zero
   MacBook Pros) log `ERROR` and exit 1 rather than looking like "no deals".
6. Installed crontab entry at `*/15` with an absolute python path.
7. Acted on a code review (6 findings, all real, all fixed):
   - blanket `except Exception` so no run can die unlogged under `cron >/dev/null`
   - unparseable price now logs `WARN` instead of dropping the listing silently
   - `config.env` tolerates inline comments and quotes (was an unlogged crash)
   - `seen.json` of the wrong shape no longer raises
   - state now records the *notified* price, killing a duplicate-alert bug where a
     listing that rose and fell back re-alerted at a price already announced
   - replaced a toothless test that asserted the quiet behaviour it claimed to guard
8. Security review (run manually — the skill needs a git remote this repo lacks).
   Two real issues, both fixed:
   - the ntfy topic was being written into world-readable `monitor.log` by
     `--test-notify`; no longer logged, and the existing log was scrubbed
   - `productDetailsUrl` was concatenated onto `https://www.apple.com` with no
     validation, so `@evil.com/x` would yield a tappable link resolving to
     evil.com. Now only plain relative paths are accepted.
   Also: `git init` + `.gitignore` so `config.env` can never be committed;
   `monitor.log`/`seen.json` tightened to mode 600.

## Tested

- ✅ `test_monitor.py` — 12/12 pass. Covers parse, nbsp normalization, threshold,
  dedupe incl. price-drop re-notify, 3 broken-page shapes, plus `check_once` end to
  end: zero-MacBook-Pros raises, failed push leaves state untouched and retries,
  price bounce does not re-alert, delisted-then-returned does re-alert, malformed
  state file survives, config parsing tolerates comments/quotes.
- ✅ Mutation-checked: deleting the zero-MacBook-Pro guard, reverting the state
  semantics, and reverting the URL validation each make the suite fail. The tests
  actually bite.
- ✅ Live run still clean after the security fixes.
- ✅ Live fetch + parse against apple.com (87 products, cheapest $2,379)
- ✅ `--test-notify` push delivered — confirmed by reading the messages back off
  the ntfy topic, not just by a 2xx response
- ✅ End-to-end match path with `MAX_PRICE=2600`: run 1 pushed 4 listings,
  run 2 pushed 0 (dedupe held), run 3 pushed exactly 1 after faking a price drop
- ✅ Runs correctly under `env -i` (cron's stripped environment)
- ✅ **cron verified firing unattended** — a clean-baseline watcher with no manual
  runs in the window caught the `20:45:01` tick writing to `monitor.log`
- ✅ SSL: python.org Python had no CA bundle → script now uses `certifi` if importable

## Not Tested / Known Gaps

- **A real sub-$1900 listing has never been seen**, because none exists today. The
  path is proven only via a raised threshold.
- Apple's anti-bot behaviour over days/weeks at a 15-min cadence is unknown. If
  fetches start failing, `monitor.log` will show `ERROR fetch failed` lines.
- cron does not fire while the Mac is asleep and does not catch up. A closed
  laptop = no checks. launchd `StartInterval` would fix this; not set up.
- No alerting if the monitor itself dies — you'd have to notice `monitor.log`
  going stale or filling with `ERROR`.
- `monitor.log` grows unbounded (~100 bytes/run ≈ 3.5 MB/year). Not rotated.

## Failed Attempts

- First live run died on `SSL: CERTIFICATE_VERIFY_FAILED`. Cause: python.org's
  macOS Python ships no CA bundle. Fixed inside the script via `certifi` rather
  than running `/Applications/Python 3.14/Install Certificates.command`, to avoid
  mutating the system Python install.
- Considered matching on Apple's `refurbClearModel == "macbookpro"` facet instead
  of the title. Rejected: agreed exactly with the title match (87 vs 87), and an
  AND of both would silently zero out if Apple renamed the facet.

## Next steps

1. **Subscribe to the topic** — install the ntfy app (iOS/Android) and add the
   topic from `config.env` (`grep NTFY_TOPIC config.env`). Until you do, alerts
   go nowhere you'll see.
2. Optional: raise `MAX_PRICE` in `config.env` — at $1900 you may wait months.
   $2,400 would catch today's cheapest.
3. Optional: switch cron → launchd if you want checks to survive sleep.
4. The repo is `git init`'d with everything staged but **not committed** — your
   CLAUDE.md wants `/karpathy-check` to run on a staged diff before a commit.
5. Check `grep ERROR monitor.log` occasionally to confirm it isn't blind.
