import type { LastFight } from '../api/types'
import { fmtDate, resultClass, resultLabel } from '../api/format'
import FighterImage from './FighterImage'

interface Props {
  lastFight: LastFight | null
  fighterName: string
  onToggleHistory: () => void
  historyOpen: boolean
}

export default function LastFightCard({
  lastFight,
  fighterName,
  onToggleHistory,
  historyOpen,
}: Props) {
  return (
    <div className="lastfight">
      <div className="lastfight-title">Last Fight</div>
      {lastFight ? (
        <>
          <div className="lastfight-fighters">
            <div className="lastfight-face">
              <FighterImage
                src={lastFight.fighter_image_url}
                alt={fighterName}
              />
            </div>
            <span className={`result-chip ${resultClass(lastFight.result)}`}>
              {resultLabel(lastFight.result)}
            </span>
            <div className="lastfight-face">
              <FighterImage
                src={lastFight.opponent_image_url}
                alt={lastFight.opponent_name}
              />
            </div>
          </div>
          <div className="lastfight-meta">
            <div style={{ color: '#0f0f0f', fontWeight: 600, marginBottom: 3 }}>
              vs {lastFight.opponent_name}
            </div>
            <div>{lastFight.event_name}</div>
            <div>{fmtDate(lastFight.date)}</div>
            {lastFight.method && (
              <div>
                {lastFight.method}
                {lastFight.round ? ` · R${lastFight.round}` : ''}
              </div>
            )}
          </div>
        </>
      ) : (
        <div className="empty-note">No completed fights.</div>
      )}
      <button className="btn-outline" onClick={onToggleHistory}>
        {historyOpen ? 'Hide Fight History' : 'View Fight History'}
      </button>
    </div>
  )
}
