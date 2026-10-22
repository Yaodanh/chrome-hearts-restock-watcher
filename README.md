# chrome-hearts-restock-watcher

Email me the moment a Chrome Hearts pop-up drop goes live, a new item appears,
or something sold out comes back.

I'm a Chrome Hearts fan. Their hoodies, pants and tees only go on sale online
during short, unannounced pop-ups and sell out fast. Refreshing the site by hand
doesn't work, so I built a tool that watches the store and emails me.

## What it does

Every 20 minutes it checks the store and sends one email for each of these events:

| Event | Meaning |
|---|---|
| **Category went live** | A clothing category such as `/hoodies` switched on: a pop-up just opened |
| **New product** | An item never seen before appeared, in stock |
| **Back in stock** | An item that was sold out can be bought again |

The first run only records what's on the site (no emails). Each event emails once.

First, get a password the program can send mail with (not your normal login password):

- **Gmail:** turn on 2-Step Verification, then create an app password at
  https://myaccount.google.com/apppasswords (16 letters).
- **163 mail:** Settings → POP3/SMTP/IMAP → turn on SMTP and copy the
  authorization code (授权码).

## Run 24/7 on GitHub Actions (computer can be off)

`.github/workflows/watch.yml` runs one check every 20 minutes on GitHub's servers.
Public repositories get Actions minutes for free.

1. Create a **public** repository on GitHub and upload this whole folder
   (including the hidden `.github` folder).
2. Repository → Settings → Secrets and variables → Actions → New repository secret:
   - `EMAIL_ADDRESS` = your Gmail (or 163) address
   - `SMTP_PASSWORD` = the app password / authorization code
   - `ALERT_TO` (optional) = a different inbox to receive alerts
3. Actions tab → enable workflows if asked → "Chrome Hearts watcher" → **Run workflow**.
   The first run records a baseline (no email). Open the run's log to check that
   categories show `in_stock` / `out_of_stock`, not all `unknown`.

Run workflow options:

- **Only send a test email**: checks your email settings.
- **Demo: replay a fake restock**: runs `demo/run.sh` with mock data, so a real
  alert email marked `[DEMO - Chrome Hearts]` arrives in about a minute. It uses its
  own state file in `demo/`, so real monitoring isn't affected.

Good to know:

- GitHub's scheduler can start runs several minutes late when it's busy.
- The "last seen" database is kept in the Actions cache between runs.
- If the site can't be read at all, the run fails and GitHub emails you about it.
- GitHub pauses scheduled workflows in public repos after 60 days with no commits;
  re-enable it in the Actions tab (or push any small change).
- Your email address and code live only in GitHub Secrets, never in the code.

## Run on your Mac instead

```bash
./start.sh
```

It installs packages the first time, asks for your address and app password,
and keeps the Mac awake while watching. Leave the Terminal window open.

Useful one-off commands (inside the project folder, after the first `./start.sh`):

```bash
export EMAIL_ADDRESS='you@gmail.com'
export SMTP_PASSWORD='your-app-password'
.venv/bin/python -m restock --test-email          # send a sample email
.venv/bin/python -m restock --once --dry-run      # one check, print instead of email
.venv/bin/python -m unittest discover -s tests -v # run the tests
```

Offline demo (prints the email instead of sending): `demo/run.sh --dry-run`.

Demo without internet: set `source.type: mock` in `config.yaml`, run once, change a
product in `data/mock_stock.json` to `in_stock`, and run again.

## How it works on chromehearts.com

The site runs on Salesforce Commerce Cloud. Everything below is in the raw HTML,
so no browser automation is needed. (Checked 2026-10-22.)

- **Switched-off categories.** Unknown paths return 404, but clothing categories
  like `/hoodies`, `/sweatshirts` and `/pants` redirect to the homepage. They
  exist and are just turned off between drops. When a pop-up opens they start
  listing products: that's the "category went live" signal.
- **Product lists.** Each category page embeds a schema.org `ItemList` with every
  product URL.
- **Stock per product.** Each product page embeds a schema.org `Product` with
  `availability` `InStock` / `OutOfStock`.
- **Stock per color/size.** Option buttons carry `selectable` or `unselectable`
  classes. In `web` mode you can watch one color, e.g. the white CH Logo Socks.
- **New categories** are picked up automatically from the sitemap and the homepage menu.

```
categories + sitemap + menu ──► live category pages ──► product pages
                                         │                     │
                                         ▼                     ▼
                              snapshots (category / product, status)
                                         │
                      previous state ──► detector ──► email
                        (SQLite)    ◄── save after sending
```

| File | Job |
|---|---|
| `restock/catalog.py` | Watches the whole store: categories, product lists, new categories |
| `restock/sources.py` | Reads one page: schema.org stock, color/size swatches; `web` and `mock` sources |
| `restock/http.py` | Polite HTTP: robots.txt, pauses between requests, never crashes on one bad page |
| `restock/detector.py` | The rules: what deserves an email |
| `restock/store.py` | Remembers the last status of each category and product |
| `restock/notifier.py` | Builds and sends the email |
| `restock/__main__.py` | Command line, loop, order of steps |

## Design decisions

- **Watch the store, not just product pages.** Pop-up items don't exist until the
  drop, so there is no page to watch in advance. Watching switched-off categories
  catches the drop itself.
- **Baseline first.** The first run records what's there without emailing,
  otherwise you'd get an email for every sock already on sale.
- **"Unknown" is its own status.** Errors, blocks or a changed layout give
  "unknown", which never alerts and never overwrites the last real status.
  A false "it's live!" email is worse than a missed check.
- **Send first, then save.** If the email fails, state isn't updated, so the next
  run retries instead of losing the alert.
- **Polite by default.** Respects robots.txt (it disallows cart, checkout and the
  raw `demandware.store` URLs, so those are never used), waits between requests,
  enforces at least 5 minutes between rounds, adds random jitter, and identifies
  itself with a clear User-Agent.
- **Swappable source.** `catalog`, `web` and `mock` share one `fetch()` interface,
  so the alert logic is tested without the real website.
- **No secrets in files.** The SMTP password only comes from an environment variable.

## Known limitations / next steps

- A 20-minute interval (plus GitHub's scheduling delay) can be too slow for drops
  that sell out in minutes. It's a trade-off between speed and load on the site.
- GitHub's servers share IP addresses with many users; if the site starts blocking
  them, runs fail (and GitHub emails me) and it would need to move elsewhere.
- If the site's HTML changes, checks return "unknown" (no false alerts, but also
  no alerts) until the parser is updated.
- Size availability on the page reflects only the default color; checking every
  combination would need the variation endpoint that robots.txt disallows.
- If a pop-up uses a brand-new URL that's neither in the sitemap nor the menu, only
  its products would be missed until it appears in one of them.

## How I used AI

<!-- Fill this in yourself, honestly. Interviewers ask about exactly this. -->

- **Tool:** Claude
- **What I asked it to do:**
- **What I accepted as-is and why:**
- **What I changed or rejected:**
- **How I verified it (tests, running it, reading the code):**
