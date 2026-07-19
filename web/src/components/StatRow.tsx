import type { CareerStats } from '../api/types'
import { fmtNum } from '../api/format'

// Big Oswald numbers with thin red underline dividers.
export default function StatRow({ stats }: { stats: CareerStats | null }) {
  const cells = [
    { label: 'Wins by KO', value: stats?.wins_by_ko ?? null },
    { label: 'Wins by Submission', value: stats?.wins_by_sub ?? null },
    { label: 'First-Round Finishes', value: stats?.first_round_finishes ?? null },
  ]
  return (
    <div className="stat-row">
      {cells.map((c) => (
        <div className="stat-cell" key={c.label}>
          <div className="stat-value num">{fmtNum(c.value)}</div>
          <div className="stat-underline" />
          <div className="stat-label">{c.label}</div>
        </div>
      ))}
    </div>
  )
}
