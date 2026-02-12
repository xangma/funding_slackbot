# Funding Slack Bot

Modular Python 3.12 service that polls funding opportunity sources, filters relevance, deduplicates, and posts new matches to Slack.

Primary source implemented: UKRI Funding Finder RSS feed (`https://www.ukri.org/opportunity/feed/`).

## Features

- CLI-first scheduled execution (`cron`, GitHub Actions, Lambda trigger, etc.)
- Source plugin interface (`Source.fetch() -> list[Opportunity]`)
- Source-agnostic normalized `Opportunity` model
- Rule-based filter engine with match reasons
- SQLite dedupe store (idempotent posting)
- Slack Incoming Webhook notifier
- Dry-run mode that prints would-post messages
- Backfill command to mark current opportunities as seen
- Unit tests with `pytest`

## Project structure

```text
src/funding_slackbot/
  cli.py
  config.py
  service.py
  models.py
  filters/
  notifiers/
  sources/
  store/
  utils/
tests/
config.example.yaml
```

## Setup

From repo root, run:

```bash
python3.12 --version
python3.12 -m venv .venv
source .venv/bin/activate

pip install -r requirements-dev.txt
pip install -e .

cp config.example.yaml config.yaml
```

Then edit `config.yaml` (keywords, storage path, etc.), and set Slack webhook:

```bash
export SLACK_WEBHOOK_URL="https://hooks.slack.com/services/..."
```

Initialize DB and test:

```bash
funding-bot --config config.yaml init-db
funding-bot --config config.yaml dry-run
```

If dry-run looks right, run for real:

```bash
funding-bot --config config.yaml run
```

Backfill current feed items as seen (no posting):

```bash
funding-bot --config config.yaml backfill --mark-seen
```

Important: in config, `slack.webhook_env_var` must be the environment variable name (for example `SLACK_WEBHOOK_URL`), not the webhook URL itself.

If you want a ready-to-use `config.yaml` tuned to your exact interests, start from `config.example.yaml` and adjust the filter keywords/exclusions.

## Filtering behavior

Rules are applied in this order:

1. `include_keywords` (if configured, at least one must match title/summary)
2. `exclude_keywords` (if any match, item is excluded)
3. Optional council/funder whitelist
4. Optional funding type whitelist
5. Optional minimum days to deadline

When a record matches, Slack includes `Why it matched: ...` with keyword or rule hits.

## Idempotency and dedupe

Dedupe key: `external_id`.

- Uses feed `id`/`guid` when available.
- Falls back to hash of canonicalized URL (tracking params stripped, fragment removed, trailing slash normalized).
- Already posted records (`posted_at` set) are skipped.

SQLite schema (`opportunities`):

- `external_id TEXT PRIMARY KEY`
- `source_id TEXT`
- `first_seen_at DATETIME`
- `posted_at DATETIME NULL`
- `title TEXT`
- `url TEXT`
- `match_reason TEXT`

## Scheduling

### Cron example

```cron
*/30 * * * * cd /path/to/funding_slackbot && /path/to/.venv/bin/funding-bot --config config.yaml run
```

### GitHub Actions example

A sample workflow is included at `.github/workflows/funding-bot-schedule.yml`.

Notes:

- Set `SLACK_WEBHOOK_URL` as a GitHub Actions secret.
- Commit your `config.yaml` (without secrets), or generate it in workflow.
- Workflow restores/saves `data/state.sqlite` via cache keys so dedupe state can persist between runs.

## Running tests

```bash
pytest
```

## Extending with new sources

Add a new source by implementing `Source` and registering it in `sources/registry.py`:

1. Implement `fetch()` returning normalized `Opportunity` instances.
2. Register via `@register_source("your_type")`.
3. Add source entry in config with `type: your_type`.

No changes to filter/store/notifier/service flow are required.
