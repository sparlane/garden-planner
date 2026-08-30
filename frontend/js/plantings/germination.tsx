import React from 'react'
import Badge from 'react-bootstrap/Badge'

import { SowingGermination } from '../types/plantings'
import { lossCauseLabel } from './loss_causes'

// Render a germination rate the server has already fixed at six decimal
// places. It arrives as a string for the same reason every other decimal does,
// so the percentage is derived once here rather than in each screen.
function formatGerminationRate(rate: string | null | undefined, fallback = '—'): string {
  if (rate === null || rate === undefined) return fallback
  return `${(Number(rate) * 100).toFixed(1)}%`
}

// Say whether a germination count is finished. The badge is the whole point of
// task 98: a provisional count and a final one are the same number until
// something on the screen distinguishes them, and the re-sow decision is made
// off that difference.
function GerminationStateBadge({ germination }: { germination: SowingGermination | null | undefined }) {
  if (!germination) return null
  if (germination.provisional) {
    return (
      <Badge bg="warning" text="dark" title="Nobody has declared this sowing finished germinating, so the count can still rise.">
        Germinating
      </Badge>
    )
  }
  return (
    <Badge bg="secondary" title={`Declared finished germinating on ${germination.closed_at ?? 'an unrecorded date'}.`}>
      Germination closed
    </Badge>
  )
}

// One line of germination figures, always carrying whether they are final.
function GerminationSummary({ germination }: { germination: SowingGermination | null | undefined }) {
  if (!germination) return null
  return (
    <span className="small">
      <GerminationStateBadge germination={germination} /> {germination.observed_count} of {germination.sown_quantity} up ({formatGerminationRate(germination.rate)})
      {germination.provisional ? ' so far' : ''}
      {!germination.provisional && germination.ungerminated > 0 && (
        <span className="text-muted">
          {' '}
          · {germination.ungerminated} never came up ({lossCauseLabel(germination.loss_cause)})
        </span>
      )}
      {germination.late_germinations > 0 && <span className="text-muted"> · {germination.late_germinations} came up after the close</span>}
    </span>
  )
}

export { GerminationStateBadge, GerminationSummary, formatGerminationRate }
