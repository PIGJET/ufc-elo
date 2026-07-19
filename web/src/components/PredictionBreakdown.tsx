import type { PredictionFactor } from '../api/types'

// Ranked factor list with a signed bar (red = favours red/A, blue = favours blue/B).
export default function PredictionBreakdown({
  factors,
}: {
  factors: PredictionFactor[]
}) {
  if (!factors || factors.length === 0) {
    return <div className="empty-note">No factor breakdown available.</div>
  }
  const max = Math.max(...factors.map((f) => Math.abs(f.contribution)), 0.0001)
  const ranked = [...factors].sort(
    (a, b) => Math.abs(b.contribution) - Math.abs(a.contribution),
  )

  return (
    <ul className="factor-list">
      {ranked.map((f) => {
        const pos = f.contribution >= 0
        const widthPct = (Math.abs(f.contribution) / max) * 50
        return (
          <li className="factor-item" key={f.name}>
            <span className="factor-desc">
              {f.description}{' '}
              <span
                style={{
                  color: pos ? 'var(--red-warm)' : 'var(--blue)',
                  fontWeight: 700,
                }}
              >
                {pos ? '+' : ''}
                {f.contribution.toFixed(2)}
              </span>
            </span>
            <div className="factor-bar-track">
              <div className="factor-bar-center" />
              <div
                className={`factor-bar-fill ${pos ? 'pos' : 'neg'}`}
                style={{ width: `${widthPct}%` }}
              />
            </div>
          </li>
        )
      })}
    </ul>
  )
}
