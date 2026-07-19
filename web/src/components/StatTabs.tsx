import { useState } from 'react'
import type { ComparisonStats, Corner } from '../api/types'
import {
  DASH,
  fmtNum,
  fmtPct,
  fmtHeight,
  fmtInches,
  fmtControl,
  ageFromDob,
} from '../api/format'

type TabKey = 'matchup' | 'winby' | 'strikes' | 'grappling'

const TABS: { key: TabKey; label: string }[] = [
  { key: 'matchup', label: 'Matchup Stats' },
  { key: 'winby', label: 'Win By' },
  { key: 'strikes', label: 'Significant Strikes' },
  { key: 'grappling', label: 'Grappling' },
]

interface Row {
  label: string
  red: string
  blue: string
}

interface Props {
  red: Corner
  blue: Corner
  redStats: ComparisonStats
  blueStats: ComparisonStats
  // optional bio for the Matchup Stats tab (height/reach/age/stance)
  redBio?: BioLite
  blueBio?: BioLite
}

export interface BioLite {
  height_in: number | null
  reach_in: number | null
  dob: string | null
  stance: string | null
}

function StatLine({ row }: { row: Row }) {
  return (
    <div className="stat-line">
      <span className="stat-line-red">{row.red}</span>
      <span className="stat-line-label">{row.label}</span>
      <span className="stat-line-blue">{row.blue}</span>
    </div>
  )
}

export default function StatTabs({
  red,
  blue,
  redStats,
  blueStats,
  redBio,
  blueBio,
}: Props) {
  const [tab, setTab] = useState<TabKey>('matchup')

  const rows: Record<TabKey, Row[]> = {
    matchup: [
      { label: 'Record', red: red.record?.display ?? DASH, blue: blue.record?.display ?? DASH },
      {
        label: 'Height',
        red: fmtHeight(redBio?.height_in ?? null),
        blue: fmtHeight(blueBio?.height_in ?? null),
      },
      {
        label: 'Weight Class',
        red: red.division ?? DASH,
        blue: blue.division ?? DASH,
      },
      {
        label: 'Reach',
        red: fmtInches(redBio?.reach_in ?? null),
        blue: fmtInches(blueBio?.reach_in ?? null),
      },
      {
        label: 'Age',
        red: ageFromDob(redBio?.dob ?? null),
        blue: ageFromDob(blueBio?.dob ?? null),
      },
      {
        label: 'Stance',
        red: redBio?.stance ?? DASH,
        blue: blueBio?.stance ?? DASH,
      },
    ],
    winby: [
      { label: 'KO / TKO', red: fmtNum(redStats.wins_by_ko), blue: fmtNum(blueStats.wins_by_ko) },
      { label: 'Submission', red: fmtNum(redStats.wins_by_sub), blue: fmtNum(blueStats.wins_by_sub) },
      { label: 'Decision', red: fmtNum(redStats.wins_by_decision), blue: fmtNum(blueStats.wins_by_decision) },
    ],
    strikes: [
      {
        label: 'Sig. Strikes Landed',
        red: fmtNum(redStats.sig_strikes_landed),
        blue: fmtNum(blueStats.sig_strikes_landed),
      },
      {
        label: 'Striking Accuracy',
        red: fmtPct(redStats.sig_strike_accuracy),
        blue: fmtPct(blueStats.sig_strike_accuracy),
      },
      {
        label: 'Strikes / Min',
        red: fmtNum(redStats.sig_strikes_per_min, 2),
        blue: fmtNum(blueStats.sig_strikes_per_min, 2),
      },
    ],
    grappling: [
      {
        label: 'Takedowns Landed',
        red: fmtNum(redStats.takedowns_landed),
        blue: fmtNum(blueStats.takedowns_landed),
      },
      {
        label: 'Takedown Accuracy',
        red: fmtPct(redStats.takedown_accuracy),
        blue: fmtPct(blueStats.takedown_accuracy),
      },
      {
        label: 'Control Time',
        red: fmtControl(redStats.control_time_seconds),
        blue: fmtControl(blueStats.control_time_seconds),
      },
    ],
  }

  return (
    <div>
      <div className="tab-row">
        {TABS.map((t) => (
          <button
            key={t.key}
            className={`tab-btn${tab === t.key ? ' active' : ''}`}
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div className="stat-table">
        {rows[tab].map((r) => (
          <StatLine row={r} key={r.label} />
        ))}
      </div>
    </div>
  )
}
