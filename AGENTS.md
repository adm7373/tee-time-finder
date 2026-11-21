# Working in this repo

Tee Time Finder is a private FastAPI app deployed as three Lambda functions: the
HTTP API, course discovery, and booking-page fingerprinting.
`docs/architecture.md` explains the split; `docs/development.md` covers local
setup.

## Checks

    venv/bin/python -m pytest
    venv/bin/python -m flake8 --max-line-length=100 --extend-ignore=E501,W503,F401 <files>
    venv/bin/python -m black <files>

## Rules

- Search reaches a course through an engine adapter: a course is a row in
  `tee-time-finder-courses`, not a subclass. To make a newly discovered course
  searchable, follow `docs/provider-adapters.md`.
- Never commit credentials, tokens, session cookies or account details. Provider
  secrets live in Secrets Manager or an untracked `.env`.
- Respect each site's terms, robots policy and rate limits. Keep requests paced;
  set `min_interval_seconds` on a new engine.
- `golfnow` and `members-only` are terminal verdicts, not engines: they tell
  discovery to stop probing, and neither counts as an adapter gap.
- To tell what a worker actually did, read its CloudWatch log group: it logs one
  line per run, per confirmed booking and per fingerprint verdict.
