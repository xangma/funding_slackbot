# Funding Source Review - 2026-06-18

Review performed against the deployed `roni1` configuration and database.

## Production sources

`roni1` currently has these enabled sources:

| Source ID | Type | Latest fetch result | Notes |
| --- | --- | ---: | --- |
| `ukri_rss` | `rss` | 20 opportunities | Working. Good title, summary, funder, and deadline coverage for currently fetched items. |
| `wellcome_schemes` | `wellcome_cms_schemes` | 15 opportunities | Working. CMS source is preferred over the older rendered page scraper. Some open or rolling schemes have no fixed closing date. |
| `innovation_funding_search` | `innovation_funding_search` | 4 opportunities | Working. Dedupe intentionally removes competitions already present in the UKRI feed. Funding amount is not currently captured. |
| `leverhulme_listings` | `leverhulme_listings` | 4 opportunities | Fetching works, but expired dates are still returned. |

Recent scheduled `run` rows on `roni1` completed successfully with no recorded source errors. The latest checked run processed 43 opportunities and recorded no errors.

## Issues to fix

1. Filter expired Leverhulme dates.
   - On 2026-06-18, `leverhulme_listings` was still returning `Research Project Grants - Outline Applications` with closing date `2026-02-27`.
   - `WellcomeCmsSchemesSource` already skips expired opportunities, but `LeverhulmeListingsSource` appends every parsed date.

2. Bring `config.example.yaml` back in line with production.
   - Production enables `innovation_funding_search`.
   - The checked-in example only lists UKRI, Wellcome CMS, and Leverhulme.

3. Improve metadata coverage where practical.
   - Innovate UK and Leverhulme currently do not capture total funding amounts.
   - Several sources have old rows in SQLite with no deadline because older fetches captured less metadata.

## Missing source candidates

These are high-value candidates for the current AI, health, NHS, bioinformatics, robotics, space, climate, and research software interests.

| Candidate | URL | Why add it |
| --- | --- | --- |
| NIHR funding opportunities | https://www.nihr.ac.uk/funding-opportunities | Direct fit for NHS, health, clinical data, public health, applied health research, and methods calls. |
| Horizon Europe / EU Funding and Tenders | https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/programmes/horizon | Large programme covering health, digital, industry, space, climate, mobility, research infrastructure, and innovation. UKRI confirms UK applicants can apply to Horizon Europe. |
| Royal Society grants | https://royalsociety.org/grants/ | Strong fit for scientific research grants, fellowships, international exchanges, industry fellowships, and data/AI-adjacent schemes. |
| Royal Academy of Engineering programmes | https://raeng.org.uk/programmes-and-prizes/programmes/ | Good fit for engineering, robotics, infrastructure, industry collaboration, fellowships, and innovation grants. |
| Academy of Medical Sciences grant schemes | https://acmedsci.ac.uk/grants-and-schemes/grant-schemes | Good biomedical and health research fit, especially early career, translational, and clinical academic schemes. |
| British Academy funding | https://www.thebritishacademy.ac.uk/funding/ | Relevant for digital society, AI impacts, policy, social science, and interdisciplinary research opportunities. |
| ARIA funding opportunities | https://aria.org.uk/funding-opportunities | High-value for frontier R&D, AI, climate, robotics, and unconventional funding calls. |
| UK Space Agency | https://www.gov.uk/government/organisations/uk-space-agency | Relevant to space science, satellite, earth observation, remote sensing, and autonomous systems interests. |
| Cancer Research UK researcher funding | https://www.cancerresearchuk.org/for-researchers | Relevant to bioinformatics, genomics, omics, clinical trials, imaging, AI for health, and cancer data opportunities. |

## Suggested implementation order

1. Fix Leverhulme expired-date filtering and update tests.
2. Add Innovate UK to `config.example.yaml`.
3. Add NIHR as the next production source.
4. Add Royal Society and Royal Academy of Engineering.
5. Add Horizon Europe after checking whether the public portal exposes a stable API endpoint suitable for unattended polling.
6. Add ARIA, UK Space Agency, Academy of Medical Sciences, British Academy, and Cancer Research UK as separate source plugins or a small shared HTML-listing source if their page structures are similar enough.

## Source module structure

The source layer should stay modular:

- `sources/_common.py`: shared HTTP retry handling, User-Agent, text cleanup, and serialization helpers.
- `sources/rss_feed.py`: generic RSS/UKRI feed source.
- `sources/wellcome.py`: Wellcome rendered-page and CMS sitemap sources.
- `sources/innovation.py`: Innovate UK competition search source.
- `sources/leverhulme.py`: Leverhulme listings and closing-date table source.
- `sources/portsmouth_jobs.py`: University of Portsmouth jobs source.
- `sources/rss_source.py`: backward-compatible import facade for older call sites.

New scrapers should live in their own module, register a factory with
`@register_source("source_type")`, and be imported from `sources/__init__.py`
so registration happens when the package is imported.

## Verification commands used

```bash
ssh roni1 'cd /home/xangma/repos/funding_slackbot && PYTHONPATH=src .venv/bin/python - <<PY
from funding_slackbot.config import load_config
from funding_slackbot.sources import create_source

config = load_config("config.yaml")
for settings in config.sources:
    source = create_source(settings)
    opportunities = source.fetch()
    print(settings.id, settings.type, len(opportunities))
PY'

.venv/bin/python -m pytest
```
