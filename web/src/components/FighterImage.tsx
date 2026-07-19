import { useState } from 'react'

interface Props {
  src: string | null | undefined
  alt: string
  className?: string
}

// Neutral dark fighter-silhouette placeholder, used consistently everywhere.
function Placeholder({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 120 160"
      xmlns="http://www.w3.org/2000/svg"
      role="img"
      aria-label="No image available"
    >
      <rect width="120" height="160" fill="#ededed" />
      <g fill="#a8a8a8">
        <circle cx="60" cy="52" r="26" />
        <path d="M20 160c0-30 18-52 40-52s40 22 40 52z" />
      </g>
    </svg>
  )
}

export default function FighterImage({ src, alt, className }: Props) {
  const [failed, setFailed] = useState(false)
  if (!src || failed) {
    return <Placeholder className={className} />
  }
  return (
    <img
      className={className}
      src={src}
      alt={alt}
      loading="lazy"
      onError={() => setFailed(true)}
    />
  )
}
