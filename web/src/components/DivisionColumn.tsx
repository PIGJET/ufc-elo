import { Link } from 'react-router-dom'
import type { Division, RankedFighter } from '../api/types'
import { fmtRating } from '../api/format'
import FighterImage from './FighterImage'

function NameLink({
  f,
  className,
}: {
  f: RankedFighter
  className?: string
}) {
  if (f.fighter_id == null) {
    return <span className={className}>{f.name}</span>
  }
  return (
    <Link to={`/fighter/${f.fighter_id}`} className={className}>
      {f.name}
    </Link>
  )
}

// The rating shown per row. Pound-for-pound columns prefer the P4P-adjusted
// number the board is ranked by, falling back to the division rating.
function ratingOf(f: RankedFighter, preferPfp: boolean): number | null {
  if (preferPfp && f.pfp_mu != null) return f.pfp_mu
  return f.rating.mu
}

export default function DivisionColumn({
  division,
  champLabel = 'Champion',
  preferPfp = false,
}: {
  division: Division
  champLabel?: string
  preferPfp?: boolean
}) {
  const champ = division.champion
  return (
    <div className="division-col">
      <div className="division-name">{division.division}</div>

      <div className="champ-block">
        {champ ? (
          <>
            <NameLink f={champ} className="champ-name" />
            <span className="champ-label">{champLabel}</span>
            <div className="champ-photo-wrap">
              <FighterImage
                src={champ.image_url}
                alt={champ.name}
                className="fighter-img"
              />
            </div>
          </>
        ) : (
          <div className="vacant">Vacant title</div>
        )}
      </div>

      <ol className="contender-list">
        {division.contenders.map((c) => (
          <li className="contender-row" key={`${c.rank}-${c.name}`}>
            <span className="contender-rank">{c.rank}</span>
            <NameLink f={c} className="contender-name" />
            <span className="contender-rating">
              {fmtRating(ratingOf(c, preferPfp))}
            </span>
          </li>
        ))}
      </ol>
    </div>
  )
}
