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
