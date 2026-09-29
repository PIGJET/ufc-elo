# Contributing to UFC Elo

Keep changes focused and explain the user-visible behavior or data issue they address.

## Report an issue

Use [GitHub Issues](https://github.com/PIGJET/ufc-elo/issues). Include the affected page, fighter or event, reproduction steps, expected versus actual behavior, and browser/device details when relevant. Screenshots help with UI problems. For disputed data, include a source link and the relevant date. Never include API keys or private environment values.

## Propose a change

1. Read the [README](README.md) and the relevant API/model documentation.
2. Work on a dedicated branch or fork; avoid modifying another contributor's worktree.
3. Keep unrelated refactoring out of the change. Include screenshots for visual changes and evidence for data/model changes.
4. Run the appropriate checks below and open a pull request explaining what changed, why, and how it was verified.
5. Wait for maintainer review before merging or deploying.

## Checks

From the repository root, with a Python environment active:

```sh
python -m pip install -r requirements-dev.txt
pytest -q
```

For frontend changes:

```sh
cd web
npm ci
npm audit
npm run lint
npm run build
```

From the repository root after building the frontend, run `python tests/smoke_deployment.py`. Also exercise the affected browser flows. Documentation-only changes need a check of links, commands, and factual claims; do not report application tests as rerun unless they were.

## Model and data changes

Preserve the distinction between ratings and matchup probabilities. Validate model changes with the documented walk-forward methodology, identify the evaluation variant, and retain speculative-comparison caveats. Record source provenance for data corrections and inspect sync errors before accepting a refreshed snapshot. Keep database, model, and report artifacts consistent.

Project code licensing has not yet been selected. Ask the maintainer about licensing before contributing material with additional reuse restrictions.
