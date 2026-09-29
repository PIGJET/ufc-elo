# Publish UFC Elo

The repository includes a `render.yaml` blueprint for one Render web service. It builds the React frontend and serves the resulting files from FastAPI alongside `/api`. A running web service makes the site available to visitors without requiring them to install anything.

## Before the first deployment

Choose the reviewed commit you want to publish. Confirm its CI checks pass, review dependency-audit findings, and verify that the data snapshot is suitable for the event dates you intend to show. A documentation update does not resolve application or dependency issues.

The site runs from the committed SQLite database and fitted model. Neither an odds API key nor a refresh token is required for a basic deployment.

## Deploy with the existing blueprint

1. Sign in to the [Render dashboard](https://dashboard.render.com/).
2. Select **New → Blueprint**, connect GitHub if necessary, and choose `PIGJET/ufc-elo` (or your own fork).
3. Select the intended release branch, normally `main`, and use the root `render.yaml`.
4. Review the proposed service, region, compute plan, and deployment settings. The blueprint currently specifies a Free Python web service.
5. Select **Deploy Blueprint** and follow the build/deploy logs.
6. After startup succeeds, open the service's assigned HTTPS URL from the dashboard. Share that exact URL; the service name alone does not guarantee a particular hostname.

Render's [Blueprint documentation](https://render.com/docs/infrastructure-as-code) describes this flow. Every web service receives an `onrender.com` URL; a custom domain is optional. See [web services](https://render.com/docs/web-services).

Do not add a “Live demo” link until the actual URL has been verified.

## Configuration reference

These values come from the repository's blueprint:

| Setting | Value |
| --- | --- |
| Runtime | Python |
| Root directory | Repository root; leave the field blank |
| Build command | `pip install -r requirements.txt && cd web && npm ci && npm run build` |
| Start command | `uvicorn api.main:app --host 0.0.0.0 --port $PORT` |
| Health check | `/api/health` |
| Blueprint Python version | `3.11.9` |
| Blueprint Node version | `22.23.2` |
| Data | Committed `data/ufc.db` and model artifacts |

CI specifies Python 3.11 and Node 22. The publishing-readiness branch pins Python 3.11.9 and Node 22.23.2 in the blueprint. Confirm the deployed runtime versions and release-check results. Render supports a `NODE_VERSION` environment variable; see [Node version configuration](https://render.com/docs/node-version). Coordinate any blueprint change through a reviewed PR.

The browser calls relative `/api` paths, so the frontend and backend belong on the same service with this setup.

## Optional environment variables

| Variable | Purpose |
| --- | --- |
| `ODDS_API_KEY` | Used by the offline odds-ingestion step. Setting it on the web service alone does not fetch odds. |
| `UFC_ELO_REFRESH_TOKEN` | Enables authenticated `POST /api/refresh` cache reloads. Leave unset unless an operator needs this feature. |

Keep secret values in the hosting environment or your local environment, not in Git. The refresh endpoint reloads in-memory caches only; it does not ingest new fight results or rankings.

## Check the published site

- Open `/api/health`; confirm `status` is `ok`, caches are loaded, and fighter counts are populated.
- Load rankings in both modes, search for a fighter, and open their profile.
- Compare a pair of fighters and browse an upcoming event. Check missing-odds and missing-prediction states.
- Open `/events`, `/matchups`, and a valid `/fighter/{id}` directly, then refresh each page. Confirm assets load and there are no API failures.
- Check the pages on a phone as well as a desktop. If the selected commit includes additional routes, check those too.
- Record the URL, deployed commit, data date, and verification results before sharing widely.

The publishing-readiness branch includes `tests/smoke_deployment.py` to exercise FastAPI and the built frontend together before deployment. Run it as part of the release checks, then check the real public URL as above.

## Hosting expectations

Free Render services sleep after 15 idle minutes, so the next visit can take about a minute to start. Local filesystem changes disappear on restart, redeployment, or spin-down. Free services cannot attach a persistent disk. See [Render's current free-service limitations](https://render.com/docs/free).

The committed database is restored with the deployment, but edits made only on the hosted filesystem are not a durable update process. Use the free plan for a demo if these limitations fit; choose an appropriate paid service if continuous availability is required.

## Updating the data

Refresh data in a separate local checkout or controlled data-maintenance job, not by depending on the hosted service's temporary disk:

1. Run the existing sync process (`python data/sync.py --all`) with any optional odds key supplied through the environment.
2. Inspect every source result. The sync script can report source failures while returning exit code zero.
3. Rebuild derived ratings and review whether style/model artifacts need regeneration. Existing entry points include `python -m elo.recompute`, `python -m elo.styles`, and `python -m elo.predict`. Follow the model reports when changing calibrated artifacts.
4. Review database integrity, event/ranking dates, and the coherence of data/model/report artifacts; run the release checks.
5. Submit the validated snapshot changes for review. After approval, deploy the reviewed commit and repeat the site checks.

Decide explicitly whether approved updates deploy automatically or manually. Blueprint auto-sync and service auto-deploy are separate settings: the former controls infrastructure changes, while the latter controls code deployments. For deliberate releases, review both settings in the Render dashboard. Preserve a known-good release and verify the available rollback procedure before relying on it.

## Finish the GitHub presentation

After verifying the public URL:

- Add it to the repository's **About → Website** field and add a “Live demo” link near the top of the README.
- Suggested description: “UFC fighter rankings and matchup predictions powered by Glicko-2, with fight histories, upcoming cards, and explainable probabilities.”
- Suggested topics: `ufc`, `mma`, `sports-analytics`, `glicko2`, `elo-rating`, `machine-learning`, `fastapi`, `react`, `typescript`, `sqlite`.
- Keep the README screenshot in sync with the deployed design.
- Choose a code license deliberately if open-source reuse is intended. Check third-party data/image terms separately.
- Create a tagged release only for the reviewed version, recording the site URL, data date, changes, and known limitations.
