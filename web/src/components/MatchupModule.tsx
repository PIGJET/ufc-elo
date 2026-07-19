import { Link } from 'react-router-dom'
import type {
  Corner,
  ComparisonStats,
  Prediction,
  RatingPreview,
  Odds,
} from '../api/types'
import { fmtRating } from '../api/format'
import FighterImage from './FighterImage'
import StatTabs from './StatTabs'
import type { BioLite } from './StatTabs'
import OddsAndPrediction from './OddsAndPrediction'

interface Props {
  red: Corner
  blue: Corner
  redStats: ComparisonStats
  blueStats: ComparisonStats
  prediction: Prediction | null
  ratingPreview: RatingPreview | null
  odds?: Odds | null
  redBio?: BioLite
  blueBio?: BioLite
}

function CornerName({ corner }: { corner: Corner }) {
  return (
    <Link to={`/fighter/${corner.id}`} className="mm-name">
      {corner.name}
    </Link>
  )
}

export default function MatchupModule({
  red,
  blue,
  redStats,
  blueStats,
  prediction,
  ratingPreview,
  odds,
  redBio,
  blueBio,
}: Props) {
  return (
    <div className="matchup-module">
      <div className="mm-header">
        <div className="mm-fighter red">
          <CornerName corner={red} />
          <span className="mm-corner-rating">{fmtRating(red.mu)}</span>
          <div className="mm-cutout">
            <FighterImage src={red.image_url} alt={red.name} />
          </div>
        </div>

        <div className="mm-vs">VS</div>

        <div className="mm-fighter blue">
          <CornerName corner={blue} />
          <span className="mm-corner-rating">{fmtRating(blue.mu)}</span>
          <div className="mm-cutout">
            <FighterImage src={blue.image_url} alt={blue.name} />
          </div>
        </div>
      </div>

      <StatTabs
        red={red}
        blue={blue}
        redStats={redStats}
        blueStats={blueStats}
        redBio={redBio}
        blueBio={blueBio}
      />

      <OddsAndPrediction
        red={red}
        blue={blue}
        odds={odds}
        prediction={prediction}
        ratingPreview={ratingPreview}
      />
    </div>
  )
}
