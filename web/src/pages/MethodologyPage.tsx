import { Link } from 'react-router-dom'

const finishRows = [
  ['KO/TKO or submission, round 1', '1.50'],
  ['KO/TKO or submission, round 2', '1.35'],
  ['KO/TKO or submission, round 3', '1.20'],
  ['KO/TKO or submission, rounds 4–5', '1.10'],
  ['Unanimous decision', '1.00'],
  ['Split or majority decision', '0.85'],
  ['Disqualification', '0.60'],
  ['Draw', '0.75'],
]

export default function MethodologyPage() {
  return (
    <main className="page methodology-page">
      <Link to="/" className="back-link">← Back to rankings</Link>
      <article className="method-article">
        <header className="method-header">
          <p className="method-kicker">Rating methodology</p>
          <h1>How ratings work</h1>
          <p className="method-lede">
            UFC Elo uses a modified Glicko-2 rating system. It tracks a fighter&apos;s
            estimated strength, how uncertain that estimate is, and how much their
            performance varies. Results update a fighter&apos;s rating within their
            division; the size of the update depends on the opponent, the result,
            and the uncertainty in both ratings.
          </p>
        </header>

        <section>
          <h2>What the number means</h2>
          <p>
            A new fighter starts at 1,500 with high uncertainty. A display such as
            1,650 ± 120 shows the rating and its rating deviation. The deviation
            describes uncertainty in the rating; it is not a win probability or a
            guaranteed range. More evidence generally makes a rating more certain.
            Time away increases uncertainty without automatically lowering the rating.
          </p>
        </section>

        <section>
          <h2>The rating update</h2>
          <div className="method-equation" aria-label="New rating equals old rating plus the clamped product of the Glicko-2 change, finish adjustment, upset correction, and stakes adjustment.">
            <span>New rating</span>
            <b>=</b>
            <span>
              old rating + clamp(Glicko-2 change × finish × upset × stakes,
              −250, +250)
            </span>
          </div>
          <p>
            The final change is limited to 250 points in either direction for one
            bout. The Glicko-2 change is the raw updated rating minus the old rating,
            not a fixed K-factor update.
          </p>

          <div className="method-table-wrap">
            <table className="method-table">
              <thead><tr><th>Result</th><th>Finish adjustment</th></tr></thead>
              <tbody>
                {finishRows.map(([result, value]) => (
                  <tr key={result}><td>{result}</td><td>{value}</td></tr>
                ))}
              </tbody>
            </table>
          </div>

          <p>
            The upset correction is <code>2.2 / (2.2 + 0.001 × pre-fight winner–loser rating gap)</code>.
            It reduces the multiplier for an expected favorite&apos;s win and increases it
            for an underdog win. For cross-pool bouts, the gap includes the engine&apos;s
            division offsets. Draws use a correction of 1.
          </p>
          <p>
            The stakes adjustment uses the highest applicable tier: undisputed title
            1.25, interim title 1.15, non-title main event 1.10, co-main event 1.05,
            or ordinary bout 1.00. These adjustments scale the rating change only;
            they do not multiply uncertainty or volatility.
          </p>
          <p>
            A win scores 1, a loss 0, and a draw 0.5 in the underlying update.
            No-contests do not change ratings.
          </p>
        </section>

        <section>
          <h2>A simple example</h2>
          <div className="method-example">
            <strong>Illustration with an assumed raw update</strong>
            <p>
              Suppose a fighter starts at 1,500 and the raw Glicko-2 update gives
              them +20 points. In a first-round finish against an equally rated
              opponent in an ordinary bout, the adjustment is 20 × 1.50 × 1 × 1 =
              30. Their new rating is 1,530.
            </p>
          </div>
          <p>
            This is not a real fight or a promise that equally rated opponents always
            produce +20. Actual Glicko-2 changes depend on deviation and volatility.
          </p>
        </section>

        <section>
          <h2>Ratings and predictions are different</h2>
          <p>
            The rating describes performance history. Matchup win probabilities come
            from a separate model that combines ratings with factors such as age,
            inactivity, physical attributes, and matchup history. A rating difference
            does not translate directly into the displayed prediction. Cross-division
            predictions are speculative, particularly for large weight gaps.
          </p>
        </section>

        <details className="method-details">
          <summary>See the underlying calculation</summary>
          <div className="method-details-body">
            <p>
              This is the per-bout Glicko-2 core after any inactivity or division
              preparation. Convert display rating <var>r</var> and deviation <var>RD</var>:
            </p>
            <ul className="equation-list">
              <li><code>μ = (r − 1500) / 173.7178</code> and <code>φ = RD / 173.7178</code></li>
              <li><code>g(φj) = 1 / √(1 + 3φj² / π²)</code></li>
              <li><code>E = 1 / (1 + exp(−g(φj)(μ − μj)))</code></li>
              <li><code>v = 1 / [g(φj)² E(1 − E)]</code></li>
              <li><code>Δ = v g(φj)(s − E)</code>, where <var>s</var> is the result score</li>
              <li>Updated volatility <code>σ′</code> is solved from the previous volatility, <code>Δ</code>, <code>v</code>, and <code>τ = 0.5</code></li>
              <li><code>φ* = √(φ² + σ′²)</code>; <code>φ′ = 1 / √(1/φ*² + 1/v)</code></li>
              <li><code>μ′ = μ + φ′² g(φj)(s − E)</code></li>
              <li>Raw display rating: <code>1500 + 173.7178μ′</code></li>
            </ul>
            <p>
              The finish, upset, and stakes adjustments are then applied to the raw
              display-rating change. Updated display RD is clamped to 40–350. See the{' '}
              <a href="https://github.com/PIGJET/ufc-elo/blob/main/elo/glicko2.py">Glicko-2 implementation</a>{' '}
              and <a href="https://github.com/PIGJET/ufc-elo/blob/main/elo/engine.py">division and multiplier handling</a>.
              The API&apos;s symmetric expected-score helper and calibrated predictor are
              separate from this rating-update equation.
            </p>
          </div>
        </details>
      </article>
    </main>
  )
}
