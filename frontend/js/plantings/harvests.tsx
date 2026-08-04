import React from 'react'
import { useQuery } from '@tanstack/react-query'
import { Card, Col, Form, Row, Table } from 'react-bootstrap'

import { getHarvestReport, getHarvests } from '../api/plantings'
import { getPlantVarieties } from '../api/plants'
import { getGardenSquares } from '../api/garden'
import { queryKeys } from '../query'
import { formatDateTime } from '../utils'
import { HarvestReportGroupBy, HarvestStatus } from '../types/plantings'
import { FamilyTotals, HarvestTable } from './harvest_list'

const GROUP_BY_OPTIONS: Array<{ value: HarvestReportGroupBy; label: string }> = [
  { value: 'variety', label: 'Variety' },
  { value: 'batch', label: 'Batch' },
  { value: 'garden_square', label: 'Garden square' },
  { value: 'garden_row', label: 'Garden row' },
  { value: 'plant', label: 'Individual plant' },
  { value: 'month', label: 'Month' },
  { value: 'year', label: 'Year' }
]

interface PeriodFilterProps {
  from: string
  to: string
  setFrom: (value: string) => void
  setTo: (value: string) => void
}

function PeriodFilter({ from, to, setFrom, setTo }: PeriodFilterProps) {
  return (
    <>
      <Col md={3}>
        <Form.Group controlId="harvest-from">
          <Form.Label>From</Form.Label>
          <Form.Control type="date" value={from} onChange={(event) => setFrom(event.target.value)} />
        </Form.Group>
      </Col>
      <Col md={3}>
        <Form.Group controlId="harvest-to">
          <Form.Label>To</Form.Label>
          <Form.Control type="date" value={to} onChange={(event) => setTo(event.target.value)} />
        </Form.Group>
      </Col>
    </>
  )
}

function HarvestsView() {
  const [variety, setVariety] = React.useState<number | ''>('')
  const [square, setSquare] = React.useState<number | ''>('')
  const [status, setStatus] = React.useState<HarvestStatus | ''>('')
  const [from, setFrom] = React.useState('')
  const [to, setTo] = React.useState('')

  const { data: varieties = [] } = useQuery({
    queryKey: queryKeys.plants.varieties,
    queryFn: ({ signal }) => getPlantVarieties(signal)
  })
  const { data: squares = [] } = useQuery({
    queryKey: queryKeys.garden.squares,
    queryFn: ({ signal }) => getGardenSquares(signal)
  })
  const { data: harvests = [], isPending } = useQuery({
    queryKey: queryKeys.plantings.harvests('', variety, square, '', status, from, to),
    queryFn: ({ signal }) =>
      getHarvests(
        {
          variety: variety === '' ? undefined : variety,
          garden_square: square === '' ? undefined : square,
          status: status === '' ? undefined : status,
          harvested_from: from || undefined,
          harvested_to: to || undefined
        },
        signal
      )
  })

  return (
    <main className="container py-3">
      <h1>Harvests</h1>
      <p>Every harvest stays on record. A mistake is reversed rather than deleted, so a reversed harvest is still listed here but counts towards no total.</p>
      <Row className="g-2 mb-3">
        <Col md={3}>
          <Form.Group controlId="harvest-filter-variety">
            <Form.Label>Variety</Form.Label>
            <Form.Select value={variety} onChange={(event) => setVariety(event.target.value === '' ? '' : Number(event.target.value))}>
              <option value="">All varieties</option>
              {varieties.map((option) => (
                <option key={option.pk} value={option.pk}>
                  {option.name}
                </option>
              ))}
            </Form.Select>
          </Form.Group>
        </Col>
        <Col md={3}>
          <Form.Group controlId="harvest-filter-square">
            <Form.Label>Garden square</Form.Label>
            <Form.Select value={square} onChange={(event) => setSquare(event.target.value === '' ? '' : Number(event.target.value))}>
              <option value="">All squares</option>
              {squares.map((option) => (
                <option key={option.pk} value={option.pk}>
                  {option.name}
                </option>
              ))}
            </Form.Select>
          </Form.Group>
        </Col>
        <PeriodFilter from={from} to={to} setFrom={setFrom} setTo={setTo} />
        <Col md={3}>
          <Form.Group controlId="harvest-filter-status">
            <Form.Label>Status</Form.Label>
            <Form.Select value={status} onChange={(event) => setStatus(event.target.value as HarvestStatus | '')}>
              <option value="">Posted and reversed</option>
              <option value="posted">Posted</option>
              <option value="reversed">Reversed</option>
            </Form.Select>
          </Form.Group>
        </Col>
      </Row>
      {isPending ? <div>Loading harvests…</div> : <HarvestTable harvests={harvests} />}
    </main>
  )
}

function YieldReportView() {
  const [groupBy, setGroupBy] = React.useState<HarvestReportGroupBy>('variety')
  const [variety, setVariety] = React.useState<number | ''>('')
  const [from, setFrom] = React.useState('')
  const [to, setTo] = React.useState('')

  const { data: varieties = [] } = useQuery({
    queryKey: queryKeys.plants.varieties,
    queryFn: ({ signal }) => getPlantVarieties(signal)
  })
  const { data: rows = [], isPending } = useQuery({
    queryKey: queryKeys.plantings.harvestReport(groupBy, '', variety, from, to),
    queryFn: ({ signal }) =>
      getHarvestReport(
        {
          group_by: groupBy,
          variety: variety === '' ? undefined : variety,
          harvested_from: from || undefined,
          harvested_to: to || undefined
        },
        signal
      )
  })

  const byPlant = groupBy === 'plant'

  return (
    <main className="container py-3">
      <h1>Yield</h1>
      <p>
        Reversed harvests are excluded. Counts, weights, and volumes are totalled separately, because they cannot be added together. Seeds sown, plants observed, and plants
        harvested out are three separate outcomes and are never divided into each other.
      </p>
      <Row className="g-2 mb-3">
        <Col md={3}>
          <Form.Group controlId="yield-group-by">
            <Form.Label>Group by</Form.Label>
            <Form.Select value={groupBy} onChange={(event) => setGroupBy(event.target.value as HarvestReportGroupBy)}>
              {GROUP_BY_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </Form.Select>
          </Form.Group>
        </Col>
        <Col md={3}>
          <Form.Group controlId="yield-variety">
            <Form.Label>Variety</Form.Label>
            <Form.Select value={variety} onChange={(event) => setVariety(event.target.value === '' ? '' : Number(event.target.value))}>
              <option value="">All varieties</option>
              {varieties.map((option) => (
                <option key={option.pk} value={option.pk}>
                  {option.name}
                </option>
              ))}
            </Form.Select>
          </Form.Group>
        </Col>
        <PeriodFilter from={from} to={to} setFrom={setFrom} setTo={setTo} />
      </Row>
      {byPlant && (
        <Card className="mb-3">
          <Card.Body className="py-2">
            <small className="text-muted">
              A harvest taken from several plants at once measured one crop, not one each. Those are reported under &ldquo;shared&rdquo; rather than added into a plant&apos;s own
              total, because splitting them between plants would invent a division nobody measured.
            </small>
          </Card.Body>
        </Card>
      )}
      {isPending ? (
        <div>Loading yield…</div>
      ) : rows.length === 0 ? (
        <p className="text-muted">No harvests match these filters.</p>
      ) : (
        <Table responsive>
          <thead>
            <tr>
              <th>{GROUP_BY_OPTIONS.find((option) => option.value === groupBy)?.label}</th>
              <th>Harvests</th>
              <th>Picked</th>
              {byPlant && <th>Shared with other plants</th>}
              <th>First</th>
              <th>Last</th>
              <th>Seeds sown</th>
              <th>Plants observed</th>
              <th>Plants harvested out</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={String(row.key)}>
                <td>{row.label}</td>
                <td>{row.harvest_count}</td>
                <td>
                  <FamilyTotals totals={row.totals} />
                </td>
                {byPlant && (
                  <td>
                    <FamilyTotals totals={row.shared_totals} />
                  </td>
                )}
                <td>{formatDateTime(row.first_harvested_at)}</td>
                <td>{formatDateTime(row.last_harvested_at)}</td>
                <td>{row.seeds_sown ?? '—'}</td>
                <td>{row.plants_observed ?? '—'}</td>
                <td>{row.plants_harvest_finished ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </main>
  )
}

export { HarvestsView, YieldReportView }
