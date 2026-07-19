import { useEffect, useState } from 'react'
import type { Division, RankedFighter, RankingsResponse } from '../api/types'
import { getRankings, getRankingsElo } from '../api/client'
import DivisionColumn from '../components/DivisionColumn'

type Mode = 'official' | 'elo'

// A pound-for-pound board has no champion; shape it like a division so it can
// reuse DivisionColumn, promoting the #1-ranked fighter into the photo header.
function p4pDivision(label: string, list: RankedFighter[]): Division {
  const sorted = [...list].sort((a, b) => a.rank - b.rank)
  const top = sorted.find((f) => f.rank === 1) ?? sorted[0] ?? null
  const contenders = sorted.filter((f) => f !== top)
  return { division: label, champion: top, contenders }
}

function RankingsBoard({ data, mode }: { data: RankingsResponse; mode: Mode }) {
  // In Elo mode the #1 slot is the highest-rated fighter, not a belt holder.
  const champLabel = mode === 'elo' ? '#1 Rated' : 'Champion'
  return (
    <div className="rankings-grid">
      {data.divisions.map((d) => (
        <DivisionColumn division={d} champLabel={champLabel} key={d.division} />
      ))}
      {data.pound_for_pound.mens.length > 0 && (
        <DivisionColumn
          division={p4pDivision("Men's Pound-for-Pound", data.pound_for_pound.mens)}
          champLabel="#1 Pound-for-Pound"
          preferPfp
          key="mens-p4p"
        />
      )}
      {data.pound_for_pound.womens.length > 0 && (
        <DivisionColumn
          division={p4pDivision("Women's Pound-for-Pound", data.pound_for_pound.womens)}
          champLabel="#1 Pound-for-Pound"
          preferPfp
          key="womens-p4p"
        />
      )}
    </div>
  )
}

export default function RankingsPage() {
  const [mode, setMode] = useState<Mode>('official')
  const [official, setOfficial] = useState<RankingsResponse | null>(null)
  const [elo, setElo] = useState<RankingsResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    getRankings()
      .then(setOfficial)
      .catch((e) => setError(String(e)))
  }, [])

  // Lazily fetch the Elo board the first time it's requested; cache thereafter.
  useEffect(() => {
    if (mode === 'elo' && elo === null) {
      getRankingsElo()
        .then(setElo)
        .catch((e) => setError(String(e)))
    }
  }, [mode, elo])

  if (error) return <div className="error-box">Failed to load rankings: {error}</div>

  const data = mode === 'official' ? official : elo

  return (
    <div className="page">
      <div className="rankings-header">
        <h1 className="page-title">
          UFC <span className="accent">Rankings</span>
        </h1>
        <div className="rank-toggle" role="group" aria-label="Ranking source">
          <button
            type="button"
            className={`rank-toggle-btn${mode === 'official' ? ' active' : ''}`}
            aria-pressed={mode === 'official'}
            onClick={() => setMode('official')}
          >
            Official
          </button>
          <button
            type="button"
            className={`rank-toggle-btn${mode === 'elo' ? ' active' : ''}`}
            aria-pressed={mode === 'elo'}
            onClick={() => setMode('elo')}
          >
            Elo
          </button>
        </div>
      </div>

      {!data ? (
        <div className="loading">Loading rankings…</div>
      ) : (
        <RankingsBoard data={data} mode={mode} />
      )}
    </div>
  )
}
