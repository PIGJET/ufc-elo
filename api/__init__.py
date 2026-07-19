"""FastAPI service for the UFC Elo project (PLAN section 7).

Read-only over ``data/ufc.db``: it serves the Rankings, Fighter-profile,
Upcoming-events and Matchup payloads the React frontend (PLAN section 8)
consumes.  All rating / prediction logic is imported from :mod:`elo` -- this
layer never recomputes a rating and never writes to the database.

Run:  uvicorn api.main:app --reload --port 8000
"""
