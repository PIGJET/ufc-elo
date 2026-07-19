import type {
  Corner,
  Odds,
  Prediction,
  RatingPreview,
} from '../api/types'
import { DASH, fmtNum } from '../api/format'

interface Props {
  red: Corner
  blue: Corner
  odds: Odds | null | undefined
  prediction: Prediction | null
  ratingPreview: RatingPreview | null
}

function fmtMoneyline(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return DASH
  return v > 0 ? `+${v}` : `${v}`
}

function delta(base: number | null, other: number | null): string {
  if (base === null || other === null) return DASH
  const d = Math.round(other - base)
  return d >= 0 ? `+${d}` : `${d}`
}

export default function OddsAndPrediction({
  red,
  blue,
  odds,
  prediction,
  ratingPreview,
}: Props) {
  const predRedWins =
    prediction != null && prediction.prob_red >= prediction.prob_blue
  const winnerName = predRedWins ? red.name : blue.name
  const conf = prediction
    ? Math.round((predRedWins ? prediction.prob_red : prediction.prob_blue) * 100)
    : null

  return (
    <div className="odds-pred">
      {/* Odds */}
      <div className="odds-row">
        <div>
          <div className="pred-label">Moneyline (Red)</div>
          {odds ? (
            <div className="odds-val" style={{ color: 'var(--red-warm)' }}>
              {fmtMoneyline(odds.red_moneyline as number | null)}
            </div>
          ) : (
            <div className="odds-unavailable">odds unavailable</div>
          )}
        </div>
        <div style={{ textAlign: 'right' }}>
          <div className="pred-label">Moneyline (Blue)</div>
          {odds ? (
            <div className="odds-val" style={{ color: 'var(--blue)' }}>
              {fmtMoneyline(odds.blue_moneyline as number | null)}
            </div>
          ) : (
            <div className="odds-unavailable">odds unavailable</div>
          )}
        </div>
      </div>

      {/* Prediction callout */}
      {prediction ? (
        <div className="pred-callout">
          <div>
            <div className="pred-label">Predicted Winner</div>
            <div className="pred-winner">{winnerName}</div>
          </div>
          <div className="pred-conf">{conf}%</div>
        </div>
      ) : (
        <div className="empty-note">
          Prediction unavailable (fighter has no rating yet).
        </div>
      )}

      {/* Rating gain/loss preview */}
      {ratingPreview && (
        <div className="rating-preview-row">
          <div className="rp-cell">
            <div className="rp-name">{red.name}</div>
            <span className="rp-win">
              WIN{' '}
              {delta(
                ratingPreview.red.current_mu,
                ratingPreview.red.if_win_mu,
              )}
            </span>{' '}
            /{' '}
            <span className="rp-loss">
              LOSS{' '}
              {delta(
                ratingPreview.red.current_mu,
                ratingPreview.red.if_loss_mu,
              )}
            </span>
          </div>
          <div className="rp-cell">
            <div className="rp-name">{blue.name}</div>
            <span className="rp-win">
              WIN{' '}
              {delta(
                ratingPreview.blue.current_mu,
                ratingPreview.blue.if_win_mu,
              )}
            </span>{' '}
            /{' '}
            <span className="rp-loss">
              LOSS{' '}
              {delta(
                ratingPreview.blue.current_mu,
                ratingPreview.blue.if_loss_mu,
              )}
            </span>
          </div>
        </div>
      )}

      {prediction && (
        <div style={{ marginTop: 10, fontSize: 11, color: 'var(--text-dim)' }}>
          Model expected score (red): {fmtNum(prediction.expected_score_raw, 2)}
        </div>
      )}
    </div>
  )
}
