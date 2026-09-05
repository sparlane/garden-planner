import React from 'react'
import { useQuery } from '@tanstack/react-query'
import { Badge, Button, Table } from 'react-bootstrap'

import { getSpecificPlantTimeline } from '../api/plantings'
import { queryKeys } from '../query'
import { PlantTimelineEntry, PlantTimelineSource } from '../types/plantings'
import { formatDateTime } from '../utils'

const PAGE_SIZE = 50

// What each source is, in the reader's words rather than the table's. The
// server names every entry from its own model's choices, so nothing here
// duplicates an event vocabulary — only the four histories themselves are
// named, which is what tells an operator why two facts sit side by side.
const SOURCE_LABELS: Record<PlantTimelineSource, string> = {
  cohort: 'Block',
  lifecycle: 'Lifecycle',
  observation: 'Nursery',
  quarantine: 'Health',
  allocation: 'Order',
  reservation: 'Order'
}

const SOURCE_VARIANTS: Record<PlantTimelineSource, string> = {
  cohort: 'secondary',
  lifecycle: 'primary',
  observation: 'success',
  quarantine: 'warning',
  allocation: 'info',
  reservation: 'info'
}

function SourceBadge({ source }: { source: PlantTimelineSource }) {
  return <Badge bg={SOURCE_VARIANTS[source]}>{SOURCE_LABELS[source]}</Badge>
}

interface PlantTimelineRowsProps {
  entries: Array<PlantTimelineEntry>
}

// A corrected entry stays on screen struck through rather than disappearing:
// all four sources correct by appending, and a reader investigating a failure
// has to be able to see what somebody was looking at when they acted.
function PlantTimelineRows({ entries }: PlantTimelineRowsProps) {
  return (
    <Table size="sm" className="mb-0">
      <thead>
        <tr>
          <th>When</th>
          <th>Where from</th>
          <th>What happened</th>
        </tr>
      </thead>
      <tbody>
        {entries.map((entry) => (
          <tr key={entry.entry_id} className={entry.corrected ? 'text-muted' : undefined}>
            <td className="text-nowrap">{formatDateTime(entry.occurred_at)}</td>
            <td>
              <SourceBadge source={entry.source} />
            </td>
            <td>
              <span className={entry.corrected ? 'text-decoration-line-through' : undefined}>{entry.label}</span>
              {entry.corrected && <span className="ms-2 fst-italic">corrected later</span>}
              {entry.corrects !== null && <span className="ms-2 fst-italic">corrects an earlier entry</span>}
              {entry.summary && <div className="small text-muted">{entry.summary}</div>}
            </td>
          </tr>
        ))}
      </tbody>
    </Table>
  )
}

interface PlantTimelineProps {
  plantPk: number
}

// The default history view: one reading of the four append-only histories a
// plant accumulates. The per-source cards below it stay, because an operator
// asking only "what did we grade it" should not have to read the rest.
function PlantTimeline({ plantPk }: PlantTimelineProps) {
  const [page, setPage] = React.useState(1)
  const { data, isPending } = useQuery({
    queryKey: queryKeys.plantings.plantTimeline(plantPk, page),
    queryFn: ({ signal }) => getSpecificPlantTimeline(plantPk, page, PAGE_SIZE, signal)
  })

  React.useEffect(() => setPage(1), [plantPk])

  if (isPending) {
    return <p className="text-muted mb-0">Loading history…</p>
  }
  if (data === undefined || data.count === 0) {
    return <p className="text-muted mb-0">Nothing has been recorded about this plant yet.</p>
  }

  const lastPage = Math.max(1, Math.ceil(data.count / PAGE_SIZE))
  return (
    <>
      <PlantTimelineRows entries={data.results} />
      {lastPage > 1 && (
        <div className="d-flex align-items-center gap-2 mt-2">
          <Button size="sm" variant="outline-secondary" disabled={page <= 1} onClick={() => setPage(page - 1)}>
            Earlier
          </Button>
          <span className="text-muted small">
            {data.count} entries · page {page} of {lastPage}
          </span>
          <Button size="sm" variant="outline-secondary" disabled={page >= lastPage} onClick={() => setPage(page + 1)}>
            Later
          </Button>
        </div>
      )}
    </>
  )
}

export { PAGE_SIZE, SOURCE_LABELS, PlantTimeline, PlantTimelineRows }
