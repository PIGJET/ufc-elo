# UFC Elo web client

React and TypeScript interface for rankings, upcoming events, fighter profiles,
and the matchup explorer. The production build is served by FastAPI from
`web/dist`; during development Vite proxies `/api` to `http://localhost:8000`.

## Commands

```bash
npm ci
npm run dev
npm run lint
npm run build
```

## Structure

- `src/api/` contains the typed API client and response models.
- `src/pages/` contains route-level ranking, event, profile, and matchup views.
- `src/components/` contains reusable charts, fighter cards, statistics, and
  prediction explanations.
- `src/index.css` contains the responsive visual system.

The UI must handle missing images, debutants without ratings, unavailable odds,
and speculative cross-division predictions without failing the page.

## Visitor analytics

Vercel Web Analytics is mounted once in `src/App.tsx` using
`@vercel/analytics/react`. It tracks page views across the client-side routes.
Enable **Web Analytics** for the UFC Elo project in the Vercel dashboard, then
deploy the reviewed change. Visitor and page-view counts appear under
**Analytics** after visitors load the deployed site; earlier visits are not
recovered. No custom events or API keys are required. Local development uses
the SDK's development mode rather than reporting production visits.
