// Typed shapes matching api/README.md

export interface Rating {
  mu: number | null
  rd: number | null
}

export interface RankedFighter {
  fighter_id: number | null
  name: string
  nickname: string | null
  image_url: string | null
  rank: number
  movement: number
  rating: Rating
  // Present only on pound-for-pound entries: the cross-division-adjusted
  // rating the P4P board is ordered by.
  pfp_mu?: number | null
}

export interface Division {
  division: string
  champion: RankedFighter | null
  contenders: RankedFighter[]
}

export interface RankingsResponse {
  snapshot_date: string
  divisions: Division[]
  pound_for_pound: {
    mens: RankedFighter[]
    womens: RankedFighter[]
  }
}

export interface SearchResult {
  id: number
  name: string
  nickname: string | null
  division: string | null
  record: string
  image_url: string | null
  mu: number | null
}

export interface SearchResponse {
  query: string
  results: SearchResult[]
}

export interface RecordObj {
  wins: number
  losses: number
  draws: number
  display: string
}

export interface FighterBio {
  id: number
  name: string
  nickname: string | null
  dob: string | null
  height_in: number | null
  reach_in: number | null
  leg_reach_in: number | null
  stance: string | null
  country: string | null
  division: string | null
  status: string | null
  image_url: string | null
  style_tag: string | null
  pre_ufc_record: string | null
}

export interface DivisionRating {
  division: string
  mu: number | null
  rd: number | null
  sigma: number | null
  pfp_mu: number | null
  n_fights: number
  last_fight: string | null
}

export interface RankChip {
  division: string
  rank: number
  movement: number
}

export interface CareerStats {
  wins_by_ko: number
  wins_by_sub: number
  wins_by_decision: number
  first_round_finishes: number
  sig_strikes_landed: number
  sig_strikes_attempted: number
  sig_strike_accuracy: number | null
  sig_strikes_per_min: number | null
  takedowns_landed: number
  takedowns_attempted: number
  takedown_accuracy: number | null
  control_time_seconds: number
  control_time_share: number | null
}

export interface EloPoint {
  date: string
  division: string
  mu: number
  rd: number
  opponent_id: number | null
  opponent_name: string | null
  result: string
}

export interface FightHistoryItem {
  fight_id: number
  opponent_id: number | null
  opponent_name: string
  event_name: string
  date: string
  result: string // win | loss | draw | nc
  method: string | null
  method_detail: string | null
  round: number | null
  time_seconds: number | null
  weight_class: string | null
  is_title: boolean
}

export interface LastFight extends FightHistoryItem {
  opponent_image_url: string | null
  fighter_image_url: string | null
}

export interface FighterProfile {
  fighter: FighterBio
  record: RecordObj
  ratings: DivisionRating[]
  rankings: RankChip[]
  career_stats: CareerStats | null
  elo_timeline: EloPoint[]
  fight_history: FightHistoryItem[]
  last_fight: LastFight | null
}

// ---- Comparison / matchup shapes ----

export interface ComparisonStats {
  wins_by_ko: number
  wins_by_sub: number
  wins_by_decision: number
  wins_by_dq: number
  first_round_finishes: number
  knockdowns: number
  sub_attempts: number
  sig_strikes_landed: number
  sig_strikes_attempted: number
  sig_strike_accuracy: number | null
  sig_strikes_per_min: number | null
  takedowns_landed: number
  takedowns_attempted: number
  takedown_accuracy: number | null
  control_time_seconds: number
  control_time_share: number | null
  total_fight_seconds: number
  fights_with_stats: number
}

export interface Corner {
  id: number
  name: string
  nickname: string | null
  image_url: string | null
  record: RecordObj
  division: string | null
  mu: number | null
  rd: number | null
}

export interface PredictionFactor {
  name: string
  contribution: number
  description: string
}

export interface Prediction {
  prob_red: number
  prob_blue: number
  expected_score_raw: number
  factors: PredictionFactor[]
  cross_division: boolean
  speculative: boolean
}

export interface RatingPreviewSide {
  division: string | null
  current_mu: number | null
  if_win_mu: number | null
  if_loss_mu: number | null
}

export interface RatingPreview {
  red: RatingPreviewSide
  blue: RatingPreviewSide
}

export interface RatingGap {
  red_pfp_mu: number | null
  blue_pfp_mu: number | null
  gap: number | null
}

export interface Odds {
  red_moneyline?: number | null
  blue_moneyline?: number | null
  book?: string | null
  [k: string]: unknown
}

export interface ComparisonBlock {
  red: Corner
  blue: Corner
  stats: { red: ComparisonStats; blue: ComparisonStats }
  prediction: Prediction | null
  rating_preview: RatingPreview | null
  rating_gap: RatingGap
}

export interface EventFight extends ComparisonBlock {
  fight_id: number
  card_position: number
  weight_class: string | null
  is_title: boolean
  is_interim_title: boolean
  is_main_event: boolean
  odds: Odds | null
}

export interface UpcomingEvent {
  id: number
  name: string
  date: string
  location: string | null
  venue: string | null
  fights: EventFight[]
}

export interface EventsResponse {
  events: UpcomingEvent[]
}

export interface MatchupResponse extends ComparisonBlock {
  cross_division: boolean
  speculative: boolean
}
