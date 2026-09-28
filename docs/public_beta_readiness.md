# Public beta readiness notes

## Dependency advisory triage

The 2026-09-28 `npm audit` reported four findings. All were resolved with
compatible patch releases in `package-lock.json`; no major upgrade was used.

| Package | Severity | Exposure and disposition |
| --- | --- | --- |
| `react-router-dom` | high | Direct browser runtime dependency. Its affected `react-router` version permits a CSRF bypass in RSC action handling. This app does not use RSC actions, but the reachable dependency was patched from 7.18.1 to 7.18.4. |
| `react-router` | high | Transitive runtime half of the same advisory above, patched to 7.18.4 with the DOM package. |
| `nanoid` | high | Vite/PostCSS build dependency. The affected custom-generator zero-size loop is not called by application code, but CI/build tooling carried it; patched from 3.3.16 to 3.3.19. |
| `postcss` | moderate | Vite build dependency. The source-map advisory requires attacker-controlled CSS/source-map input during a build; production serves prebuilt assets. Patched from 8.5.19 to 8.5.28. |

Re-run `cd web && npm audit` before release. The expected result for this
candidate is zero known vulnerabilities.

## Model claims

The 60.3% accuracy / 0.6588 log-loss headline belongs to the corner-aware
walk-forward evaluator. The API and website serve `predict_fight`, an
order-invariant projection that averages both corner assignments; its recorded
walk-forward result is 60.1% accuracy / 0.6637 log loss. The persisted model is
then fit on all available history for production use. These historical metrics
are not promises of future performance.

## Checked-in data snapshot

Verified locally on 2026-09-28: completed results run through 2026-09-19,
official rankings are dated 2026-09-23, ratings are calculated as of
2026-09-23, and the upcoming-card source last ran on 2026-09-23. The database
contains eight upcoming-status cards through 2026-11-14; one is dated
2026-09-26 and is now stale, but the public endpoint correctly filters past
dates. Refresh and inspect per-source errors before treating the card list as
live.

## Deployment verification

Build and test the same combined process used by Render:

```powershell
pip install -r requirements-dev.txt
pytest -q
cd web
npm ci
npm audit
npm run lint
npm run build
cd ..
python tests/smoke_deployment.py
```

The smoke test starts Uvicorn on an ephemeral local port and checks populated
health data, rankings JSON, built hashed assets, direct SPA navigation, a real
fighter route, and JSON 404 behavior for unknown API paths.

Deploy only after approval. Create/apply the Render blueprint from
`render.yaml`, record the deployed commit and runtime versions, then run the
same public URL checks. Roll back by redeploying the last verified commit; do
not treat an unverified build as a rollback target.
