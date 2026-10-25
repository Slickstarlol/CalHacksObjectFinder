import { useState } from 'react'
import { mediaUrl } from './format'

export default function Shot({ path, alt, className = '' }) {
  const [brokenPath, setBrokenPath] = useState('')
  const src = mediaUrl(path)
  const failed = Boolean(src) && brokenPath === src
  if (!src || failed) {
    return <div className={`ph ${className}`.trim()}>No photo</div>
  }
  return (
    <img
      className={className}
      src={src}
      alt={alt}
      onError={() => setBrokenPath(src)}
    />
  )
}
