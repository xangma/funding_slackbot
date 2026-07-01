# Funding Slack Bot

Modular Python 3.12 service that polls funding opportunity sources, screens relevance with rules or optional LLM assessment, deduplicates, and posts new matches to Slack.

Primary sources implemented: UKRI Funding Finder RSS, Wellcome CMS scheme pages, Innovate UK funding search, Leverhulme listings, NIHR, Horizon Europe, Royal Society, Royal Academy of Engineering, Academy of Medical Sciences, ARIA, UK Space Agency, and Cancer Research Horizons. British Academy is registered but disabled in the example config because its funding pages are Cloudflare-protected for non-browser requests.

## Features

- CLI-first scheduled execution (`cron`, GitHub Actions, Lambda trigger, etc.)
- Source plugin interface (`Source.fetch() -> list[Opportunity]`)
- Source-agnostic normalized `Opportunity` model
- Rule-based filtering plus optional LLM relevance assessment
- SQLite dedupe store (idempotent posting)
- Slack Incoming Webhook notifier
- Optional local LLM digest grouping via an OpenAI-compatible chat API
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

`config.yaml` is intentionally ignored by git. Keep local or production-specific filters, storage paths, and endpoints there; update `config.example.yaml` when changing shared defaults.

## Filtering behavior

By default, rules are applied in this order:

1. `include_keywords` (if configured, at least one must match title/summary)
2. `exclude_keywords` (if any match, item is excluded)
3. Optional council/funder whitelist
4. Optional funding type whitelist
5. Optional minimum days to deadline

With `llm.enabled` and `llm.assess_opportunities` enabled, each candidate that is not already posted, reserved, or queued for a digest is classified by the local model instead of the rule-based filter. The model receives normalized opportunity fields plus the configured include/exclude/council/type/deadline criteria. If the model request fails or returns unusable JSON, the bot falls back to the rules above.

When a record matches, Slack includes `Why it matched: ...` with rule hits or the LLM decision. Grouping is separate from assessment: when `llm.enabled` and `llm.group_opportunities` are both true, matched items are grouped into one digest. `digest.batch_new_opportunities` can queue them until the configured digest time or pending-item threshold. If grouping fails, deterministic metadata grouping is used so matches are not dropped.

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

The `opportunities` table stores the composite key, seen/posting timestamps, post status, title/link/source metadata, optional LLM assessment JSON, deadline metadata, and reminder state. Primary key: `(source_id, external_id)`.

The SQLite database also stores a `runs` table with one row per completed production `run` command, including timestamps, counts, success state, and a compact error summary. SQLite connections use a busy timeout and WAL journaling, and the CLI takes a per-database lock file before running commands to avoid overlapping cron/manual runs.

Before deploying a schema-changing release on `roni1`, back up the current database, then run `funding-bot --config config.yaml init-db` once on the host to apply the migration before the scheduled job resumes.

## Local LLM assessment, grouping, and reminders

Enable the bot-side features in `config.yaml`:

```yaml
llm:
  enabled: true
  base_url: http://127.0.0.1:8001/v1
  model: qwen3.6
  assess_opportunities: true
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

With `llm.enabled` and `llm.assess_opportunities` enabled, the filter asks the local model to classify each candidate against the configured interests and exclusions, using deterministic rules only on request or parsing failures. The grouping prompt sends normalized opportunity fields and asks the model to return JSON containing only group headings, summaries, and exact opportunity IDs. Slack rendering still uses the original source titles, links, funders, deadlines, and match reasons.

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
- Generate `config.yaml` in the workflow, or copy it from a secret-backed artifact. Do not commit production `config.yaml`.
- Workflow restores/saves `data/state.sqlite` via cache keys so dedupe state can persist between runs.

## Running tests

```bash
pytest
```

## Extending with new sources

Add a new source as a small module under `src/funding_slackbot/sources/`.
Each scraper should own only the parsing and source-specific normalization for
one external site or API. Put reusable HTTP, text cleanup, and serialization
helpers in `_common.py` instead of copying them between scrapers.

1. Implement `Source.fetch()` returning normalized `Opportunity` instances.
2. Register a factory in the same module via `@register_source("your_type")`.
3. Import the source class from `sources/__init__.py` so registration happens at startup.
4. Add a source entry in config with `type: your_type`.
5. Add focused parser tests using fixture HTML or JSON.

No changes to filter/store/notifier/service flow are required.

Sources can be disabled without deleting their configuration by setting `enabled: false`.
