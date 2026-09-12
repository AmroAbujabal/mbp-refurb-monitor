# MacBook Pro refurb monitor

Watches Apple Canada's refurbished store and pushes a phone notification when a
**MacBook Pro** is listed under a price threshold (default **$1900 CAD**).

**The threshold compares Apple's listed pre-tax price.** GST/HST/PST is added at
checkout, so a $1,899 hit is roughly $2,050–2,145 out of pocket depending on
province. Set `MAX_PRICE` accordingly if you're budgeting a final total. CAD is
assumed from the `/ca/` path in `STORE_URL`; the script does not verify the
returned currency.

## Stack

Python 3 (stdlib only — no pip install needed). Single script, no framework.
`certifi` is used only if importable, to work around python.org Python on macOS
shipping no CA bundle.

## Files

| File | Purpose |
|---|---|
| `monitor.py` | The whole thing: fetch, parse, filter, dedupe, notify, log |
| `config.env` | `NTFY_TOPIC` + `MAX_PRICE`. chmod 600. Env vars of the same name override it |
| `seen.json` | State: `{partNumber: price}` for items currently under the threshold |
| `monitor.log` | One line per run (append-only) |
| `test_monitor.py` | 12 self-checks, no pytest: `python3 test_monitor.py` |

## Architecture

Apple embeds the entire product grid as JSON in the page:
`window.REFURB_GRID_BOOTSTRAP = {...}`. We locate that marker and use
`json.JSONDecoder().raw_decode(html, offset)` to parse exactly one JSON value
starting mid-document — no brace counting, no HTML regex, no browser needed.

Products are collected by **walking the parsed tree** for any dict carrying
`title` + `partNumber` + `price`, rather than hardcoding a nesting path. Apple
reshuffling their JSON layout won't break it; only renaming those fields will.
Results are deduped by `partNumber` (the page lists some parts more than once).

The page ships the *whole* Mac catalogue — MacBook Air, iMac, Studio Display,
MacBook Neo — so filtering is mandatory. Titles are normalized (non-breaking
space `\xa0` → space, non-breaking hyphen `‑` → `-`) before matching
`"macbook pro"` case-insensitively. Without that normalization some listings
silently never match. "MacBook Neo … A18 **Pro** chip" correctly does *not* match.

### Failure is loud, never silent

A monitor that quietly stops finding things is worse than one that crashes.
These all log `ERROR` and exit 1:

- marker missing (Apple redesigned the page)
- grid JSON won't parse
- JSON parsed but **zero products** found
- products found but **zero MacBook Pros** — treated as a structure change, *not* as "no deals"
- network/SSL failure
- **anything else** — a blanket `except Exception` logs `ERROR unexpected: ...`.
  This matters because cron sends stderr to `/dev/null`: without it, an unhandled
  traceback would kill the run leaving *no trace at all* in the log.

Check `monitor.log` for `ERROR` to see if it's been blind.

### Notification dedupe

Notify when a part is **new**, or when its price is **lower** than when we last
notified. `seen.json` is pruned each run to only currently-matching items, so a
listing that disappears and returns will notify again.

State is written **only after a successful push** — if ntfy is down, the run
leaves state untouched and retries next time rather than swallowing the alert.

State stores the price we last *notified* at, not the last price observed. A
listing that ticks up and back down therefore stays quiet instead of re-alerting
at a price you already heard about.

Deliberate asymmetry: that only holds while the listing stays *under* the
threshold. If it rises above `MAX_PRICE` it drops out of `matches` and gets
pruned, so returning to its old price alerts again. Chosen so a listing that
sells out and comes back is never silently missed — the cost is an occasional
repeat for one that hovers around the threshold.

## How to run

```bash
python3 monitor.py                 # run once (this is what cron calls)
python3 monitor.py --test-notify   # send a test push, verify your topic
python3 test_monitor.py            # self-check
MAX_PRICE=2600 python3 monitor.py  # temporarily widen threshold to see it fire
```

## Scheduling (cron, macOS)

Installed line — note the **absolute** python path, because cron's `PATH` is minimal:

```
*/15 * * * * /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 /Users/amrabujabal/mbp-monitor/monitor.py >/dev/null 2>&1
```

- Install/edit: `crontab -e`  · Verify: `crontab -l`  · Remove: `crontab -r`
- Output is sent to `/dev/null` because the script already logs to `monitor.log`;
  without that, cron mails you locally every 15 minutes.
- Verify it's alive: `tail -f monitor.log` and wait for the next :00/:15/:30/:45.

**macOS caveat:** cron does not run while the Mac is asleep and does not catch up
on missed runs. If you want misses to fire on wake, use a launchd agent with
`StartInterval` instead — same command, `~/Library/LaunchAgents/`. Not set up;
cron was what was asked for.

## Gotchas

- `config.env` tolerates `KEY=value # comment` and `KEY="quoted"`. It is read
  before the logging try/except, so a malformed value is a loud `SystemExit`.
- **This repo is public.** The topic must exist in exactly two places: `config.env`
  (local, gitignored) and the `NTFY_TOPIC` GitHub Actions secret. Never in a
  tracked file, a doc, or a log. It leaked into HANDOFF.md once and had to be
  rotated.
- `config.env` holds the ntfy topic — anyone who knows the topic string can read
  the notifications. It's chmod 600 and should never be committed.
- ntfy topics are public-by-obscurity. That's fine for refurb prices; don't put
  anything sensitive through it.
- Inventory genuinely fluctuates between runs (86 vs 87 products minutes apart) —
  that's Apple, not a bug.

## Security notes

- **The ntfy topic is the only credential.** Anyone who knows it can read your
  notifications and publish to them. It lives in `config.env` (mode 600) and is
  deliberately **never written to `monitor.log`** — the log is a lower-trust file.
  `test_topic_never_reaches_the_log` enforces this by AST-scanning every `log()`
  call site for the topic (a plain grep can't: `notify()` legitimately builds
  `f"https://ntfy.sh/{topic}"`).
- **`product_url()` rejects anything that isn't a plain relative path.** Apple's
  `productDetailsUrl` is untrusted input that ends up in the tappable `Click`
  header; naive concatenation turns `@evil.com/x` into
  `https://www.apple.com@evil.com/x`, which resolves to *evil.com*. Anything
  suspicious falls back to the store URL.
- TLS verification is on (certifi-backed `create_default_context`). Never swap
  this for an unverified context to "fix" a cert error.
- `config.env`, `seen.json` and `monitor.log` are gitignored and mode 600.

## Session hygiene

One feature or bug per session. Update `HANDOFF.md`, then `/clear`.
