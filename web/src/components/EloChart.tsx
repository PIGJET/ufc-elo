import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts'
import type { EloPoint } from '../api/types'
import { fmtDate } from '../api/format'

interface ChartDatum {
  date: string
  mu: number
  opponent: string
  result: string
}

function EloTooltip({
  active,
  payload,
}: {
  active?: boolean
  payload?: Array<{ payload: ChartDatum }>
}) {
  if (!active || !payload || payload.length === 0) return null
  const p = payload[0].payload
  return (
    <div
      style={{
        background: '#ffffff',
        border: '1px solid #d20a0a',
        borderRadius: 4,
        padding: '8px 12px',
        fontSize: 12,
        color: '#0f0f0f',
        boxShadow: '0 4px 12px rgba(0,0,0,0.12)',
      }}
    >
      <div style={{ fontWeight: 700, fontFamily: 'Oswald, sans-serif' }}>
        {Math.round(p.mu)}
      </div>
      <div style={{ color: '#6b6b6b' }}>{fmtDate(p.date)}</div>
      {p.opponent && (
        <div style={{ color: '#444', marginTop: 2 }}>
          {p.result.toUpperCase()} vs {p.opponent}
        </div>
      )}
    </div>
  )
}

export default function EloChart({ timeline }: { timeline: EloPoint[] }) {
  if (!timeline || timeline.length === 0) {
    return <div className="empty-note">No rating history available.</div>
  }
  const data: ChartDatum[] = timeline.map((t) => ({
    date: t.date,
    mu: t.mu,
    opponent: t.opponent_name ?? '',
    result: t.result ?? '',
  }))

  return (
    <div className="chart-wrap">
      <ResponsiveContainer width="100%" height={300}>
        <LineChart data={data} margin={{ top: 8, right: 18, left: 0, bottom: 4 }}>
          <CartesianGrid stroke="#e2e2e2" strokeDasharray="3 3" />
          <XAxis
            dataKey="date"
            tickFormatter={(d) => String(d).slice(0, 4)}
            stroke="#9a9a9a"
            tick={{ fontSize: 11, fill: '#6b6b6b' }}
            minTickGap={30}
          />
          <YAxis
            stroke="#9a9a9a"
            tick={{ fontSize: 11, fill: '#6b6b6b' }}
            domain={['dataMin - 40', 'dataMax + 40']}
            width={48}
            allowDecimals={false}
            tickFormatter={(v) => String(Math.round(Number(v)))}
          />
          <Tooltip content={<EloTooltip />} />
          <Line
            type="monotone"
            dataKey="mu"
            stroke="#d20a0a"
            strokeWidth={2.5}
            dot={{ r: 2.5, fill: '#d20a0a' }}
            activeDot={{ r: 5 }}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
