# Funding Slack Bot

Modular Python 3.12 service that polls funding opportunity sources, filters relevance, deduplicates, and posts new matches to Slack.

Primary sources implemented: UKRI Funding Finder RSS, Wellcome CMS scheme pages, Innovate UK funding search, and Leverhulme listings.

## Features

- CLI-first scheduled execution (`cron`, GitHub Actions, Lambda trigger, etc.)
- Source plugin interface (`Source.fetch() -> list[Opportunity]`)
- Source-agnostic normalized `Opportunity` model
- Rule-based filter engine with match reasons
- SQLite dedupe store (idempotent posting)
- Slack Incoming Webhook notifier
- Optional local LLM grouping via llama.cpp's OpenAI-compatible API
- Deadline reminder digests for posted opportunities nearing their closing date
- Retry handling for transient RSS/Slack failures
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

When a record matches, Slack includes `Why it matched: ...` with keyword or rule hits. With `llm.group_opportunities` enabled and a reachable local model, a run posts one grouped digest instead of one Slack message per opportunity. If `digest.batch_new_opportunities` is enabled, new matches are queued first and posted together at the configured digest time, or earlier when the pending queue reaches the configured size. If the local LLM is unavailable or returns invalid JSON, the bot falls back to deterministic metadata grouping so opportunities are not dropped.

Plain keywords match whole words or phrases. Add `*` inside a keyword to match a
word family intentionally, for example `genom*` matches `genome`, `genomic`, and
`genomics`, while `"*omics"` matches terms such as `transcriptomics` and
`proteomics`. Quote YAML entries that start with `*`; leading wildcards are broad
and match any whole word with that ending.

## Idempotency and dedupe

Dedupe key: `(source_id, external_id)`.

- Uses feed `id`/`guid` when available.
- Falls back to hash of canonicalized URL (tracking params stripped, fragment removed, trailing slash normalized).
- Already posted records (`posted_at` set) are skipped.
- Queued digest records (`post_status = pending_digest`) are not posted individually and are not re-filtered on later runs.
- Records are marked `posting` before Slack is called. This prevents a repost if Slack succeeds but the final SQLite update fails.

Existing SQLite databases from the original single-column `external_id` schema are migrated automatically by `init-db` or `run`. The migration preserves existing `posted_at` values and marks those rows as `posted`.

SQLite schema (`opportunities`):

- `source_id TEXT`
- `external_id TEXT`
- `first_seen_at DATETIME`
- `posted_at DATETIME NULL`
- `title TEXT`
- `url TEXT`
- `match_reason TEXT`
- `post_status TEXT`
- `last_post_attempt_at DATETIME NULL`
- `post_error TEXT NULL`
- `last_seen_at DATETIME NULL`
- `closing_date DATETIME NULL`
- `opening_date DATETIME NULL`
- `funder TEXT NULL`
- `funding_type TEXT NULL`
- `total_fund TEXT NULL`
- `reminder_status TEXT`
- `last_reminder_attempt_at DATETIME NULL`
- `reminder_posted_at DATETIME NULL`
- `reminder_error TEXT NULL`

Primary key: `(source_id, external_id)`.

Before deploying a schema-changing release on `roni1`, back up the current database, then run `funding-bot --config config.yaml init-db` once on the host to apply the migration before the scheduled job resumes.

## Local LLM grouping and reminders

Enable the bot-side features in `config.yaml`:

```yaml
llm:
  enabled: true
  base_url: http://127.0.0.1:8001/v1
  model: qwen3.6
  group_opportunities: true

digest:
  batch_new_opportunities: true
  post_at_hour: 9
  timezone: Europe/London
  post_when_pending_count_reaches: 10

reminders:
  enabled: true
  days_before_deadline: 7
  max_reminders_per_run: 10
```

The LLM integration expects an OpenAI-compatible chat endpoint such as recent llama.cpp router mode:

```bash
curl http://127.0.0.1:8001/health
curl http://127.0.0.1:8001/v1/models
```

The grouping prompt sends normalized opportunity fields and asks the model to return JSON containing only group headings, summaries, and exact opportunity IDs. Slack rendering still uses the original source titles, links, funders, deadlines, and match reasons.

With digest batching enabled, matching opportunities move into `pending_digest` state. The scheduled job keeps fetching every run, but only posts pending digest items once the local time is at or after `digest.post_at_hour` and at least one queued item was first seen before that day's cutoff. This means opportunities that arrive after the morning digest are normally grouped into the next day's digest. The queue also flushes immediately if it reaches `digest.post_when_pending_count_reaches`.

Deadline reminders are driven by SQLite state. The bot records closing dates when opportunities are fetched, then posts one reminder digest for already-posted opportunities whose deadline is within `reminders.days_before_deadline`. Reminder rows are claimed before Slack posting and marked `posted` afterward, so a grant should only be reminded once.

Use dry-run before enabling either feature on the production Slack webhook:

```bash
funding-bot --config config.yaml dry-run
```

See `docs/llama-router-setup.md` for the `len` llama.cpp router configuration and the recommended `roni1` replication steps.

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

Sources can be disabled without deleting their configuration by setting `enabled: false`.
