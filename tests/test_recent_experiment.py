import pandas as pd
import numpy as np

from elo.experiment_recent import RECENT_NAMES, rolling_features


def test_rolling_features_exclude_current_same_day_and_future_results():
    fights = pd.DataFrame([
        dict(id=1,date='2020-01-01',fighter_red_id=1,fighter_blue_id=2,
             winner_id=1,round=1,time_seconds=60,method='KO/TKO'),
        dict(id=2,date='2020-01-01',fighter_red_id=1,fighter_blue_id=3,
             winner_id=1,round=1,time_seconds=60,method='KO/TKO'),
        dict(id=3,date='2020-02-01',fighter_red_id=1,fighter_blue_id=4,
             winner_id=4,round=3,time_seconds=300,method='U-DEC'),
    ])
    initial = rolling_features(fights,{},{}).set_index('fight_id')
    assert np.allclose(initial.loc[[1,2],list(RECENT_NAMES)],0)
    assert initial.loc[3,'win_rate'] > 0
    changed = fights.copy()
    changed.loc[2,'winner_id'] = 1
    assert initial.equals(rolling_features(changed,{},{}).set_index('fight_id'))
    # Changing a past outcome must affect a subsequent fight, but no same-day one.
    changed.loc[0,'winner_id'] = 2
    updated = rolling_features(changed,{},{}).set_index('fight_id')
    assert np.allclose(updated.loc[[1,2]],initial.loc[[1,2]])
    assert updated.loc[3,'win_rate'] < initial.loc[3,'win_rate']


def test_missing_stats_do_not_invent_striking_advantages():
    fights = pd.DataFrame([
        dict(id=1,date='2020-01-01',fighter_red_id=1,fighter_blue_id=2,
             winner_id=1,round=1,time_seconds=60,method='KO/TKO'),
        dict(id=2,date='2020-02-01',fighter_red_id=1,fighter_blue_id=2,
             winner_id=2,round=1,time_seconds=60,method='KO/TKO'),
    ])
    result = rolling_features(fights,{},{}).iloc[1]
    for name in ('sig_net','sig_accuracy','sig_defense','td_rate','td_defense',
                 'control_share','stats_coverage'):
        assert result[name] == 0
