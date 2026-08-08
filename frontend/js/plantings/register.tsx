import React from 'react'
import { useQuery } from '@tanstack/react-query'
import { Alert, Badge, Button, ButtonGroup, Card, Col, Form, Row } from 'react-bootstrap'

import { getNurseryRegister, getProductionBatches } from '../api/plantings'
import { getPlantVarieties } from '../api/plants'
import { queryKeys } from '../query'
import { NurseryRegisterFilters, NurseryRegisterOrdering, NurseryRegisterTotals, PlantLifecycleState } from '../types/plantings'
import { STATE_LABELS } from './lifecycle'
import { EMPTY_SELECTION, RegisterSelection, RegisterTable } from './register_list'

const PAGE_SIZE = 50

const ORDERING_OPTIONS: Array<{ value: NurseryRegisterOrdering; label: string }> = [
  { value: '-age', label: 'Newest first' },
  { value: 'age', label: 'Oldest first' },
  { value: 'variety', label: 'Crop A–Z' },
  { value: 'location', label: 'Location' },
  { value: 'state', label: 'State' },
  { value: 'batch', label: 'Batch' },
  { value: '-cost', label: 'Most expensive first' }
]

const LOCATION_OPTIONS: Array<{ value: NonNullable<NurseryRegisterFilters['location_type']>; label: string }> = [
  { value: 'seed_tray_cell', label: 'In a seed tray' },
  { value: 'garden_square', label: 'Planted out' },
  { value: 'none', label: 'Not placed anywhere' }
]

// The states an operator asks about first. The rest are reachable through the
// totals, which always report every state whether or not it is filtered on.
const STATE_OPTIONS: Array<PlantLifecycleState> = ['growing', 'available', 'retained', 'failed', 'culled', 'donated', 'harvested']

interface TotalsProps {
  totals: NurseryRegisterTotals
}

// These describe the whole filter, not the page below them, which is the
// reason the register exists: "what have I got" is not a question about
// whichever fifty rows happen to be on screen.
function RegisterTotals({ totals }: TotalsProps) {
  const tiles: Array<{ label: string; value: number; variant?: string }> = [
    { label: 'Matching', value: totals.total },
    { label: 'Growing', value: totals.growing },
    { label: 'Available', value: totals.available, variant: 'text-success' },
    { label: 'Unresolved', value: totals.unresolved },
    { label: 'Retained', value: totals.retained },
    { label: 'Lost', value: totals.failed + totals.culled, variant: 'text-danger' }
  ]
  return (
    <Row className="g-2 mb-3">
      {tiles.map((tile) => (
        <Col key={tile.label} xs={6} md={2}>
          <Card body className="py-2">
            <div className="text-muted small">{tile.label}</div>
            <div className={`fs-5 ${tile.variant ?? ''}`}>{tile.value}</div>
          </Card>
        </Col>
      ))}
    </Row>
  )
}

interface SelectionBarProps {
  selection: RegisterSelection
  matching: number
  setSelection: (selection: RegisterSelection) => void
}

function SelectionBar({ selection, matching, setSelection }: SelectionBarProps) {
  const selected = selection.mode === 'filter' ? matching : selection.ids.length
  if (selected === 0) {
    return null
  }
  return (
    <Alert variant="light" className="d-flex align-items-center gap-3 py-2">
      <span>
        <Badge bg="secondary">{selected}</Badge> {selection.mode === 'filter' ? 'plants matching these filters are selected.' : 'plants selected.'}
      </span>
      {selection.mode === 'ids' && matching > selected && (
        <Button size="sm" variant="outline-secondary" onClick={() => setSelection({ mode: 'filter' })}>
          Select all {matching} matching
        </Button>
      )}
      <Button size="sm" variant="outline-secondary" onClick={() => setSelection(EMPTY_SELECTION)}>
        Clear
      </Button>
    </Alert>
  )
}

function NurseryRegisterView() {
  const [search, setSearch] = React.useState('')
  const [variety, setVariety] = React.useState<number | ''>('')
  const [batch, setBatch] = React.useState<number | ''>('')
  const [states, setStates] = React.useState<Array<PlantLifecycleState>>([])
  const [locationType, setLocationType] = React.useState<NurseryRegisterFilters['location_type'] | ''>('')
  const [germinatedFrom, setGerminatedFrom] = React.useState('')
  const [germinatedTo, setGerminatedTo] = React.useState('')
  const [ordering, setOrdering] = React.useState<NurseryRegisterOrdering>('-age')
  const [page, setPage] = React.useState(1)
  const [selection, setSelection] = React.useState<RegisterSelection>(EMPTY_SELECTION)

  const filters: NurseryRegisterFilters = {
    search: search || undefined,
    variety: variety === '' ? undefined : variety,
    batch: batch === '' ? undefined : batch,
    state: states.length > 0 ? states : undefined,
    location_type: locationType === '' ? undefined : locationType,
    germinated_from: germinatedFrom || undefined,
    germinated_to: germinatedTo || undefined,
    ordering,
    page,
    page_size: PAGE_SIZE
  }

  // Changing what is being asked for invalidates a selection made against the
  // previous question, so it is dropped rather than silently re-pointed.
  function narrow<T>(setter: (value: T) => void): (value: T) => void {
    return (value: T) => {
      setter(value)
      setPage(1)
      setSelection(EMPTY_SELECTION)
    }
  }

  const { data: varieties = [] } = useQuery({
    queryKey: queryKeys.plants.varieties,
    queryFn: ({ signal }) => getPlantVarieties(signal)
  })
  const { data: batches = [] } = useQuery({
    queryKey: queryKeys.plantings.batches('active', '', '', false),
    queryFn: ({ signal }) => getProductionBatches({ status: 'active' }, signal)
  })
  const { data: register, isPending } = useQuery({
    queryKey: queryKeys.plantings.register(filters),
    queryFn: ({ signal }) => getNurseryRegister(filters, signal)
  })

  function toggleState(state: PlantLifecycleState) {
    narrow(setStates)(states.includes(state) ? states.filter((entry) => entry !== state) : [...states, state])
  }

  const lastPage = register === undefined ? 1 : Math.max(1, Math.ceil(register.count / PAGE_SIZE))

  return (
    <main className="container py-3">
      <h1>Plant register</h1>
      <p>Every plant currently on the books, with what it is, how old it is, where it is standing, and what it has cost so far.</p>

      <Row className="g-2 mb-3">
        <Col md={3}>
          <Form.Group controlId="register-search">
            <Form.Label>Search</Form.Label>
            <Form.Control type="search" placeholder="Plant number, batch code, or crop" value={search} onChange={(event) => narrow(setSearch)(event.target.value)} />
          </Form.Group>
        </Col>
        <Col md={3}>
          <Form.Group controlId="register-variety">
            <Form.Label>Variety</Form.Label>
            <Form.Select value={variety} onChange={(event) => narrow(setVariety)(event.target.value === '' ? '' : Number(event.target.value))}>
              <option value="">All varieties</option>
              {varieties.map((entry) => (
                <option key={entry.pk} value={entry.pk}>
                  {entry.name}
                </option>
              ))}
            </Form.Select>
          </Form.Group>
        </Col>
        <Col md={3}>
          <Form.Group controlId="register-batch">
            <Form.Label>Batch</Form.Label>
            <Form.Select value={batch} onChange={(event) => narrow(setBatch)(event.target.value === '' ? '' : Number(event.target.value))}>
              <option value="">All active batches</option>
              {batches.map((entry) => (
                <option key={entry.pk} value={entry.pk}>
                  {entry.code}
                </option>
              ))}
            </Form.Select>
          </Form.Group>
        </Col>
        <Col md={3}>
          <Form.Group controlId="register-location">
            <Form.Label>Where</Form.Label>
            <Form.Select
              value={locationType ?? ''}
              onChange={(event) => narrow(setLocationType)(event.target.value === '' ? '' : (event.target.value as NonNullable<NurseryRegisterFilters['location_type']>))}
            >
              <option value="">Anywhere</option>
              {LOCATION_OPTIONS.map((entry) => (
                <option key={entry.value} value={entry.value}>
                  {entry.label}
                </option>
              ))}
            </Form.Select>
          </Form.Group>
        </Col>
        <Col md={3}>
          <Form.Group controlId="register-germinated-from">
            <Form.Label>Germinated from</Form.Label>
            <Form.Control type="date" value={germinatedFrom} onChange={(event) => narrow(setGerminatedFrom)(event.target.value)} />
          </Form.Group>
        </Col>
        <Col md={3}>
          <Form.Group controlId="register-germinated-to">
            <Form.Label>Germinated to</Form.Label>
            <Form.Control type="date" value={germinatedTo} onChange={(event) => narrow(setGerminatedTo)(event.target.value)} />
          </Form.Group>
        </Col>
        <Col md={3}>
          <Form.Group controlId="register-ordering">
            <Form.Label>Sort by</Form.Label>
            <Form.Select value={ordering} onChange={(event) => narrow(setOrdering)(event.target.value as NurseryRegisterOrdering)}>
              {ORDERING_OPTIONS.map((entry) => (
                <option key={entry.value} value={entry.value}>
                  {entry.label}
                </option>
              ))}
            </Form.Select>
          </Form.Group>
        </Col>
      </Row>

      <div className="d-flex flex-wrap gap-2 mb-3">
        {STATE_OPTIONS.map((state) => (
          <Button key={state} size="sm" variant={states.includes(state) ? 'primary' : 'outline-secondary'} onClick={() => toggleState(state)}>
            {STATE_LABELS[state]}
          </Button>
        ))}
      </div>

      {register !== undefined && <RegisterTotals totals={register.totals} />}
      {register !== undefined && <SelectionBar selection={selection} matching={register.count} setSelection={setSelection} />}

      {isPending ? (
        <div>Loading plants…</div>
      ) : (
        <>
          <RegisterTable rows={register?.results ?? []} selection={selection} setSelection={setSelection} />
          <div className="d-flex align-items-center gap-3">
            <ButtonGroup>
              <Button variant="outline-secondary" disabled={page <= 1} onClick={() => setPage(page - 1)}>
                Previous
              </Button>
              <Button variant="outline-secondary" disabled={page >= lastPage} onClick={() => setPage(page + 1)}>
                Next
              </Button>
            </ButtonGroup>
            <span className="text-muted">
              Page {page} of {lastPage}
            </span>
          </div>
        </>
      )}
    </main>
  )
}

export { NurseryRegisterView }
