"""UFC Elo rating engine (Glicko-2 core + documented MOV/stakes multipliers).

See ``docs/PLAN.md`` section 3 for the specification and ``elo/config.py`` for
every tunable constant.  Public entry points:

    elo.glicko2   -- textbook Glicko-2 core (unit tested)
    elo.engine    -- chronological replay, preview_update, expected_score
    elo.recompute -- CLI: full recompute of elo_history + ratings_current

Prediction layer (Phase 2, PLAN sections 4-5) -- reads ratings, never writes:

    elo.styles    -- CLI: classify fighting style -> fighters.style_tag
    elo.features  -- strictly pre-fight feature builder (backtest + live)
    elo.predict   -- logistic prediction layer; predict_fight() for the API
    elo.backtest  -- CLI: walk-forward metrics, calibration, ablation
"""
