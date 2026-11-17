"""Read-only recent-form experiment; never rewrites production weights or DB.

Run: python -m elo.experiment_recent --output-dir path/to/results
Select on 2022-2025 annual folds, then evaluate 2026 once. Same-day bouts
cannot enter one another's rolling statistics. See the generated caveats.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from data.db import DB_PATH, get_conn
from elo.backtest import accuracy, brier, log_loss, vanilla_elo_predictions
from elo.features import DEFAULT_IMPUTE, FEATURE_NAMES, build_training_frame
from elo.predict import (SYMMETRIC_FEATURES, fit_coefficients,
                         recency_weights, walk_forward_predictions)
from elo.recompute import load_offsets

RECENT_NAMES = ('sig_net', 'sig_accuracy', 'sig_defense', 'td_rate',
                'td_defense', 'control_share', 'win_rate', 'finish_rate',
                'experience', 'opponent_rating', 'stats_coverage')
STYLE_NAMES = ('style_wr_vs_str', 'style_gr_vs_str', 'style_gr_vs_wr')


def summary(history):
    recent = list(history)[-5:]
    measured = [r for r in recent if r['has_stats']]
    # Fixed neutral priors; smoothing keeps one-fight samples from dominating.
    minutes = sum(r['minutes'] for r in measured)
    total = lambda key: sum(r[key] for r in measured)
    return np.array([
        total('sig_net') / (minutes + 10),
        (total('sl') + 10) / (total('sa') + 25),
        1 - (total('opp_sl') + 10) / (total('opp_sa') + 25),
        15 * total('td') / (minutes + 15),
        1 - (total('opp_td') + 1) / (total('opp_tda') + 3),
        (total('control') + 60) / (minutes * 60 + 300),
        (sum(r['won'] for r in recent) + 1) / (len(recent) + 2),
        (sum(r['finish'] for r in recent) + 1) / (len(recent) + 4),
        np.log1p(len(history)),
        (sum(r['opp_rating'] - 1500 for r in recent) / (len(recent) + 3)) / 400,
        len(measured) / 5,
    ], dtype=float)


def recent_frame(conn):
    fights = pd.read_sql_query('''
        SELECT f.*, e.date FROM fights f JOIN events e ON e.id=f.event_id
        WHERE f.result_current IN ('win','dq','draw','nc')
        ORDER BY e.date, f.id''', conn)
    stats = {(r['fight_id'], r['fighter_id']): dict(r)
             for r in conn.execute('SELECT * FROM fight_stats')}
    ratings = {(r['fight_id'], r['fighter_id']): r['mu_pre']
               for r in conn.execute('SELECT fight_id,fighter_id,mu_pre FROM elo_history')}
    return rolling_features(fights, stats, ratings)


def rolling_features(fights, stats, ratings):
    history = defaultdict(list)
    records = []
    for _, same_day in fights.groupby('date', sort=True):
        pending = []
        for f in same_day.to_dict('records'):
            red, blue, fid = f['fighter_red_id'], f['fighter_blue_id'], f['id']
            diff = summary(history[red]) - summary(history[blue])
            records.append({'fight_id': fid, **dict(zip(RECENT_NAMES, diff))})
            duration = max(1, ((f['round'] or 1) - 1) * 300 + (f['time_seconds'] or 0))
            for fighter, opponent in ((red, blue), (blue, red)):
                s, o = stats.get((fid, fighter)), stats.get((fid, opponent))
                val = lambda row, key: (row or {}).get(key) or 0
                won = float(f['winner_id'] == fighter)
                pending.append((fighter, {
                    'has_stats': s is not None and o is not None,
                    'minutes': duration / 60,
                    'sl': val(s,'sig_strikes_landed'), 'sa': val(s,'sig_strikes_attempted'),
                    'opp_sl': val(o,'sig_strikes_landed'), 'opp_sa': val(o,'sig_strikes_attempted'),
                    'sig_net': val(s,'sig_strikes_landed') - val(o,'sig_strikes_landed'),
                    'td': val(s,'takedowns_landed'), 'opp_td': val(o,'takedowns_landed'),
                    'opp_tda': val(o,'takedowns_attempted'),
                    'control': val(s,'control_time_seconds'),
                    'won': won, 'finish': won * (f['method'] in ('KO/TKO','SUB')),
                    'opp_rating': ratings.get((fid,opponent),1500),
                }))
        for fighter, record in pending:
            history[fighter].append(record)
    return pd.DataFrame(records)


def metrics(y, p):
    return {'n': len(y), 'accuracy': accuracy(p,y),
            'log_loss': log_loss(p,y), 'brier': brier(p,y)}


def fit_predict(train, test, variant, year):
    names = list(FEATURE_NAMES) + ([] if variant == 'base' else list(RECENT_NAMES))
    x = train[names].to_numpy(float)
    xt = test[names].to_numpy(float)
    reverse = xt.copy()
    for i, name in enumerate(names):
        if name not in SYMMETRIC_FEATURES:
            reverse[:,i] *= -1
    weights = recency_weights(train.year.to_numpy(), year)
    if variant == 'base':
        coef, intercept = fit_coefficients(x,train.y.to_numpy(),weights)
        logits, swapped = xt @ coef + intercept, reverse @ coef + intercept
    else:
        if variant == 'recent_logistic':
            model = make_pipeline(StandardScaler(), LogisticRegression(C=1,max_iter=2000))
            model.fit(x,train.y,logisticregression__sample_weight=weights)
        else:
            model = HistGradientBoostingClassifier(max_iter=120,max_leaf_nodes=7,
                learning_rate=0.05,l2_regularization=10,min_samples_leaf=40,
                early_stopping=False,random_state=17)
            model.fit(x,train.y,sample_weight=weights)
        probability = np.clip(model.predict_proba(xt)[:,1],1e-9,1-1e-9)
        backwards = np.clip(model.predict_proba(reverse)[:,1],1e-9,1-1e-9)
        logits = np.log(probability / (1-probability))
        swapped = np.log(backwards / (1-backwards))
    sigmoid = lambda z: 1 / (1+np.exp(-z))
    return sigmoid(logits), sigmoid((logits-swapped)/2)


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True,exist_ok=True)
    conn = get_conn()
    try:
        original = build_training_frame(conn,load_offsets())
        wf = walk_forward_predictions(original)
        elo = vanilla_elo_predictions(conn)
        reproduction = {
            'existing_corner_backtest': metrics(wf.y.to_numpy(),wf.p_pred.to_numpy()),
            'standard_elo': metrics(wf.y.to_numpy(),wf.fight_id.map(elo).to_numpy()),
            'always_red_accuracy': float(wf.y.mean()),
        }
        print('Existing backtest:',json.dumps(reproduction),flush=True)
        original['year'] = original.date.str[:4].astype(int)
        # Avoid using full-career style tags or full-roster imputation means.
        frame = build_training_frame(conn,load_offsets(),impute=DEFAULT_IMPUTE)
        frame[list(STYLE_NAMES)] = 0.0
        frame = frame.merge(recent_frame(conn),on='fight_id',validate='one_to_one')
        frame['year'] = frame.date.str[:4].astype(int)
    finally:
        conn.close()
    predictions = []
    variants = ('base','recent_logistic','recent_boosting')
    for year in range(2022,2026):
        train,test = frame[frame.year < year],frame[frame.year == year]
        for variant in variants:
            corner,sym = fit_predict(train,test,variant,year)
            part = test[['fight_id','date','year','y']].copy()
            part['variant'],part['corner'],part['sym'] = variant,corner,sym
            predictions.append(part)
            print(year,variant,metrics(test.y.to_numpy(),sym),flush=True)
    development = pd.concat(predictions,ignore_index=True)
    dev_scores = {v: metrics(g.y.to_numpy(),g.sym.to_numpy())
                  for v,g in development.groupby('variant')}
    # Freeze selection before generating any holdout predictions.
    selected = min(variants[1:],key=lambda v: dev_scores[v]['log_loss'])
    train,test = frame[frame.year < 2026],frame[frame.year == 2026]
    final = {}
    for variant in ('base',selected):
        corner,sym = fit_predict(train,test,variant,2026)
        part = test[['fight_id','date','year','y']].copy()
        part['variant'],part['corner'],part['sym'] = variant,corner,sym
        predictions.append(part)
        final[variant] = {'matchup': metrics(test.y.to_numpy(),sym),
                          'corner': metrics(test.y.to_numpy(),corner)}
    # Context only: the legacy feature set has the known point-in-time caveats.
    legacy_test = original[original.year == 2026]
    legacy_corner,legacy_sym = fit_predict(original[original.year < 2026],legacy_test,'base',2026)
    final['legacy_features_reference'] = {
        'matchup': metrics(legacy_test.y.to_numpy(),legacy_sym),
        'corner': metrics(legacy_test.y.to_numpy(),legacy_corner)}
    all_predictions = pd.concat(predictions,ignore_index=True)
    all_predictions.to_csv(args.output_dir/'predictions.csv',index=False)
    held = all_predictions[all_predictions.year == 2026]
    pairs = held.pivot(index=['fight_id','date','y'],columns='variant',values='sym').reset_index()
    # Paired bootstrap by event date, preserving same-card dependence.
    y = pairs.y.to_numpy()
    credit = lambda p: np.where(p == .5,.5,(p > .5) == y).astype(float)
    gain = credit(pairs[selected].to_numpy()) - credit(pairs['base'].to_numpy())
    grouped = pd.DataFrame({'date':pairs.date,'gain':gain}).groupby('date').gain.agg(['sum','count'])
    rng = np.random.default_rng(17)
    draws = rng.integers(0,len(grouped),size=(2000,len(grouped)))
    changes = grouped['sum'].to_numpy()[draws].sum(axis=1) / grouped['count'].to_numpy()[draws].sum(axis=1)
    result = {'reproduction':reproduction,'development':dev_scores,'selected':selected,
        'database_sha256':hashlib.sha256(DB_PATH.read_bytes()).hexdigest(),
        'holdout_2026':final,'accuracy_gain_95pct_event_bootstrap':np.quantile(changes,[.025,.975]).tolist(),
        'holdout_end':test.date.max(),'holdout_event_dates':len(grouped),
        'caveats':[
            'Retrospective temporal evaluation, not a prospective untouched trial: 2026 history was previously examined in this project.',
            'Shared current physical-attribute snapshots and previously fitted division offsets/configuration remain; this is not a complete point-in-time data audit.',
            'Experimental comparison disables full-career style tags and uses fixed imputation constants for both arms.',
            'Recent statistics use strictly earlier dates, including draws and no contests in performance history.',
            'No betting-odds features. No production weights or database modified. Selection uses 2022-2025 matchup log loss only.',
        ]}
    (args.output_dir/'metrics.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2),flush=True)


if __name__ == '__main__':
    main()
