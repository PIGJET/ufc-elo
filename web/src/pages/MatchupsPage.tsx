import { useEffect, useState } from 'react'
import type { MatchupResponse, SearchResult } from '../api/types'
import { getMatchup, getFighter } from '../api/client'
import type { BioLite } from '../components/StatTabs'
import FighterSearchSelect from '../components/FighterSearchSelect'
import MatchupModule from '../components/MatchupModule'
import PredictionBreakdown from '../components/PredictionBreakdown'
import { fmtNum } from '../api/format'

export default function MatchupsPage() {
  const [red, setRed] = useState<SearchResult | null>(null)
  const [blue, setBlue] = useState<SearchResult | null>(null)
  const [data, setData] = useState<MatchupResponse | null>(null)
  const [redBio, setRedBio] = useState<BioLite | undefined>(undefined)
  const [blueBio, setBlueBio] = useState<BioLite | undefined>(undefined)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!red || !blue) {
      setData(null)
      return
    }
    if (red.id === blue.id) {
      setError('Pick two different fighters.')
      setData(null)
      return
    }
    setLoading(true)
    setError(null)
    getMatchup(red.id, blue.id)
      .then(setData)
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false))

    // enrich the Matchup Stats tab with bio (height/reach/age/stance)
    getFighter(red.id)
      .then((p) =>
        setRedBio({
          height_in: p.fighter.height_in,
          reach_in: p.fighter.reach_in,
          dob: p.fighter.dob,
          stance: p.fighter.stance,
        }),
      )
      .catch(() => setRedBio(undefined))
    getFighter(blue.id)
      .then((p) =>
        setBlueBio({
          height_in: p.fighter.height_in,
          reach_in: p.fighter.reach_in,
          dob: p.fighter.dob,
          stance: p.fighter.stance,
        }),
      )
      .catch(() => setBlueBio(undefined))
  }, [red, blue])

  const gap = data?.rating_gap?.gap

  return (
    <div className="page">
      <h1 className="page-title">
        Fighter <span className="accent">Matchups</span>
      </h1>

      <div className="matchup-pickers">
        <FighterSearchSelect
          label="Red Corner"
          corner="red"
          selected={red}
          onSelect={setRed}
        />
        <FighterSearchSelect
          label="Blue Corner"
          corner="blue"
          selected={blue}
          onSelect={setBlue}
        />
      </div>

      {error && <div className="error-box">{error}</div>}
      {loading && <div className="loading">Running prediction…</div>}

      {!red || !blue ? (
        <div className="empty-note">
          Choose two fighters to compare (cross-division is allowed).
        </div>
      ) : null}

      {data && (
        <>
          {(data.speculative || data.cross_division) && (
            <div className="spec-note">
              <strong>Speculative comparison.</strong>{' '}
              {data.cross_division
                ? 'These fighters compete in different divisions, so a cross-division rating offset is applied. '
                : ''}
              Treat this projection with caution — the ratings are thin or the
              matchup is hypothetical.
            </div>
          )}

          {gap != null && (
            <div className="rating-gap-line">
              Rating gap: <b>{fmtNum(Math.abs(gap))}</b>{' '}
              {gap >= 0 ? 'in favor of Red' : 'in favor of Blue'}
            </div>
          )}

          <MatchupModule
            red={data.red}
            blue={data.blue}
            redStats={data.stats.red}
            blueStats={data.stats.blue}
            prediction={data.prediction}
            ratingPreview={data.rating_preview}
            redBio={redBio}
            blueBio={blueBio}
          />

          <h2 className="section-title" style={{ marginTop: 30 }}>
            Prediction Factors
          </h2>
          {data.prediction ? (
            <PredictionBreakdown factors={data.prediction.factors} />
          ) : (
            <div className="empty-note">
              No prediction available (a fighter has no rating yet).
            </div>
          )}
        </>
      )}
    </div>
  )
}
