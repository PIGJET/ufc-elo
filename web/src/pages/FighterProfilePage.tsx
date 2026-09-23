import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import type { FighterProfile } from '../api/types'
import { getFighter } from '../api/client'
import {
  DASH,
  fmtRating,
  fmtRatingWithRd,
  fmtDate,
} from '../api/format'
import FighterImage from '../components/FighterImage'
import StatRow from '../components/StatRow'
import EloChart from '../components/EloChart'
import LastFightCard from '../components/LastFightCard'

export default function FighterProfilePage() {
  const { id } = useParams<{ id: string }>()
  const [result, setResult] = useState<{
    id: string
    data?: FighterProfile
    error?: string
  } | null>(null)
  const [historyOpenFor, setHistoryOpenFor] = useState<string | null>(null)

  useEffect(() => {
    if (!id) return
    let activeRequest = true
    getFighter(id)
      .then((data) => {
        if (activeRequest) setResult({ id, data })
      })
      .catch((e) => {
        if (activeRequest) setResult({ id, error: String(e) })
      })
    return () => {
      activeRequest = false
    }
  }, [id])

  const currentResult = result && result.id === id ? result : null
  const data = currentResult?.data
  const error = currentResult?.error
  const historyOpen = historyOpenFor === id

  if (error) return <div className="error-box">Failed to load fighter: {error}</div>
  if (!data) return <div className="loading">Loading fighter…</div>

  const { fighter, record, ratings, rankings, career_stats, elo_timeline } = data
  const primary = ratings[0] ?? null
  const rankChip = rankings[0] ?? null

  return (
    <div className="page">
      <div className="hero">
        {/* Left: identity */}
        <div>
          <div className="chips">
            {rankChip && (
              <span className="chip red">
                {rankChip.rank === 0 ? 'Champion' : `#${rankChip.rank}`}{' '}
                {rankChip.division}
              </span>
            )}
            {primary?.division && !rankChip && (
              <span className="chip">{primary.division} Division</span>
            )}
            {fighter.status && (
              <span className="chip">{fighter.status}</span>
            )}
            {fighter.style_tag && (
              <span className="chip">{fighter.style_tag}</span>
            )}
          </div>

          {fighter.nickname && (
            <div className="hero-nick">"{fighter.nickname}"</div>
          )}
          <h1 className="hero-name">{fighter.name}</h1>
          <div className="hero-sub">
            {(primary?.division ?? fighter.division ?? 'UFC')} ·{' '}
            <strong>UFC record</strong> {record.display}
            {fighter.pre_ufc_record
              ? ` · Pre-UFC ${fighter.pre_ufc_record}`
              : ''}
          </div>

          <div className="hero-rating num">
            {fmtRating(primary?.mu)}{' '}
            <small>± {primary?.rd != null ? Math.round(primary.rd) : DASH}</small>
          </div>
          <div className="hero-rating-label">
            {primary?.division ?? ''} Rating
          </div>
          <div className="hero-pfp">
            P4P Rating: {fmtRating(primary?.pfp_mu)}
          </div>
        </div>

        {/* Center: cutout */}
        <div className="hero-cutout">
          <FighterImage src={fighter.image_url} alt={fighter.name} />
        </div>

        {/* Right: last fight */}
        <LastFightCard
          lastFight={data.last_fight}
          fighterName={fighter.name}
          onToggleHistory={() =>
            setHistoryOpenFor((current) => (current === id ? null : (id ?? null)))
          }
          historyOpen={historyOpen}
        />
      </div>

      {/* Career stat row */}
      <StatRow stats={career_stats} />

      {/* Fight history (toggle) */}
      {historyOpen && (
        <>
          <h2 className="section-title">Fight History</h2>
          {data.fight_history.length === 0 ? (
            <div className="empty-note">No fights on record.</div>
          ) : (
            <ul className="history-list">
              {data.fight_history.map((h) => (
                <li className="history-item" key={h.fight_id}>
                  <span className={`result-chip ${h.result.toLowerCase()}`}>
                    {h.result}
                  </span>
                  {h.opponent_id != null ? (
                    <Link
                      to={`/fighter/${h.opponent_id}`}
                      className="history-opp"
                    >
                      {h.opponent_name}
                    </Link>
                  ) : (
                    <span className="history-opp">{h.opponent_name}</span>
                  )}
                  <span className="history-meta">
                    {h.method ?? DASH}
                    {h.round ? ` · R${h.round}` : ''}
                    <br />
                    {h.event_name} · {fmtDate(h.date)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}

      {/* Rating over time */}
      <h2 className="section-title">
        Rating Over Time{' '}
        <span style={{ color: 'var(--text-grey)', fontSize: 13 }}>
          ({fmtRatingWithRd(primary?.mu, primary?.rd)})
        </span>
      </h2>
      <EloChart timeline={elo_timeline} />
    </div>
  )
}
