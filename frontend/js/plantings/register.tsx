import React from 'react'
import { useQuery } from '@tanstack/react-query'
import { Alert, Badge, Button, ButtonGroup, Card, Col, Form, Row } from 'react-bootstrap'

import { getLocations } from '../api/locations'
import { getInventoryItems } from '../api/inventory'
import { getGrowthStages, getNurseryRegister, getPlantGrades, getProductionBatches } from '../api/plantings'
import { getPlantVarieties } from '../api/plants'
import { getSeedTrayGenerations, getSeedTrays } from '../api/seedtrays'
import { queryKeys } from '../query'
import { NurseryRegisterFilters, NurseryRegisterOrdering, NurseryRegisterTotals, PlantLifecycleState } from '../types/plantings'
import { STATE_LABELS } from './lifecycle'
import { EMPTY_SELECTION, RegisterSelection, RegisterTable } from './register_list'
import { BulkOperationPanel } from './bulk_operations'

const PAGE_SIZE = 50

const ORDERING_OPTIONS: Array<{ value: NurseryRegisterOrdering; label: string }> = [
  { value: '-age', label: 'Newest first' },
  { value: 'age', label: 'Oldest first' },
  { value: 'variety', label: 'Crop A–Z' },
  { value: 'location', label: 'Location' },
  { value: 'standing_at', label: 'Where it is standing' },
  { value: 'state', label: 'State' },
  { value: 'state_since', label: 'In this state longest' },
  { value: 'first_ready', label: 'First offered' },
  { value: 'batch', label: 'Batch' },
  { value: '-cost', label: 'Most expensive first' },
  { value: 'expected_ready', label: 'Ready date' }
]

const LOCATION_OPTIONS: Array<{ value: NonNullable<NurseryRegisterFilters['location_type']>; label: string }> = [
  { value: 'seed_tray_cell', label: 'In a seed tray' },
  { value: 'garden_square', label: 'Planted out' },
  { value: 'location', label: 'Standing on its own' },
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
    { label: 'Quarantined', value: totals.quarantined, variant: 'text-warning' },
    { label: 'Reserved', value: totals.reserved, variant: 'text-primary' },
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
  const [location, setLocation] = React.useState<number | ''>('')
  const [seedTray, setSeedTray] = React.useState<number | ''>('')
  const [generation, setGeneration] = React.useState<number | ''>('')
  const [germinatedFrom, setGerminatedFrom] = React.useState('')
  const [germinatedTo, setGerminatedTo] = React.useState('')
  const [ordering, setOrdering] = React.useState<NurseryRegisterOrdering>('-age')
  const [stage, setStage] = React.useState<number | ''>('')
  const [grade, setGrade] = React.useState<number | ''>('')
  const [container, setContainer] = React.useState<number | ''>('')
  const [readyFrom, setReadyFrom] = React.useState('')
  const [readyTo, setReadyTo] = React.useState('')
  const [stageOverdue, setStageOverdue] = React.useState(false)
  const [quarantined, setQuarantined] = React.useState<boolean | undefined>(undefined)
  const [reserved, setReserved] = React.useState<boolean | undefined>(undefined)
  const [page, setPage] = React.useState(1)
  const [selection, setSelection] = React.useState<RegisterSelection>(EMPTY_SELECTION)

  const filters: NurseryRegisterFilters = {
    search: search || undefined,
    variety: variety === '' ? undefined : variety,
    batch: batch === '' ? undefined : batch,
    state: states.length > 0 ? states : undefined,
    location_type: locationType === '' ? undefined : locationType,
    location: location === '' ? undefined : location,
    seed_tray: seedTray === '' ? undefined : seedTray,
    generation: generation === '' ? undefined : generation,
    germinated_from: germinatedFrom || undefined,
    germinated_to: germinatedTo || undefined,
    stage: stage === '' ? undefined : stage,
    grade: grade === '' ? undefined : grade,
    container: container === '' ? undefined : container,
    expected_ready_from: readyFrom || undefined,
    expected_ready_to: readyTo || undefined,
    stage_overdue: stageOverdue || undefined,
    quarantined,
    reserved,
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
  const { data: stages = [] } = useQuery({ queryKey: ['growth-stages'], queryFn: ({ signal }) => getGrowthStages(signal) })
  const { data: grades = [] } = useQuery({ queryKey: ['plant-grades'], queryFn: ({ signal }) => getPlantGrades(signal) })
  const { data: containers = [] } = useQuery({
    queryKey: ['inventory', 'pot-containers'],
    queryFn: ({ signal }) => getInventoryItems({ category: 'pot_container' }, signal)
  })
  const { data: locations = [] } = useQuery({
    queryKey: queryKeys.locations.list('active'),
    queryFn: ({ signal }) => getLocations(signal, true)
  })
  const { data: seedTrays = [] } = useQuery({
    queryKey: queryKeys.seedTrays.trays,
    queryFn: ({ signal }) => getSeedTrays(signal)
  })
  const { data: generations = [] } = useQuery({
    queryKey: queryKeys.seedTrays.generations(seedTray || 0),
    queryFn: ({ signal }) => getSeedTrayGenerations(seedTray as number, signal),
    enabled: seedTray !== ''
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
          <Form.Label>Reservation</Form.Label>
          <Form.Select
            value={reserved === undefined ? '' : String(reserved)}
            onChange={(event) => narrow(setReserved)(event.target.value === '' ? undefined : event.target.value === 'true')}
          >
            <option value="">Any status</option>
            <option value="true">Reserved</option>
            <option value="false">Not reserved</option>
          </Form.Select>
        </Col>
        <Col md={3}>
          <Form.Group controlId="register-seed-tray">
            <Form.Label>Tray</Form.Label>
            <Form.Select
              value={seedTray}
              onChange={(event) => {
                narrow(setSeedTray)(event.target.value === '' ? '' : Number(event.target.value))
                setGeneration('')
              }}
            >
              <option value="">Any tray</option>
              {seedTrays.map((entry) => (
                <option key={entry.pk} value={entry.pk}>
                  Tray #{entry.pk}
                </option>
              ))}
            </Form.Select>
          </Form.Group>
        </Col>
        <Col md={3}>
          <Form.Group controlId="register-generation">
            <Form.Label>Tray fill</Form.Label>
            <Form.Select value={generation} disabled={seedTray === ''} onChange={(event) => narrow(setGeneration)(event.target.value === '' ? '' : Number(event.target.value))}>
              <option value="">Any fill</option>
              {generations.map((entry) => (
                <option key={entry.pk} value={entry.pk}>
                  {entry.code}
                </option>
              ))}
            </Form.Select>
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
          <Form.Group controlId="register-standing-in">
            <Form.Label>Standing in</Form.Label>
            <Form.Select value={location} onChange={(event) => narrow(setLocation)(event.target.value === '' ? '' : Number(event.target.value))}>
              <option value="">Any location</option>
              {locations.map((entry) => (
                <option key={entry.pk} value={entry.pk}>
                  {entry.full_name}
                </option>
              ))}
            </Form.Select>
            <Form.Text muted>Includes anything on a bench or bay inside the place you pick.</Form.Text>
          </Form.Group>
        </Col>
        <Col md={3}>
          <Form.Group controlId="register-germinated-from">
            <Form.Label>Germinated from</Form.Label>
            <Form.Control type="date" value={germinatedFrom} onChange={(event) => narrow(setGerminatedFrom)(event.target.value)} />
          </Form.Group>
        </Col>
        <Col md={3}>
          <Form.Group controlId="register-stage">
            <Form.Label>Growth stage</Form.Label>
            <Form.Select value={stage} onChange={(event) => narrow(setStage)(event.target.value === '' ? '' : Number(event.target.value))}>
              <option value="">Any stage</option>
              {stages.map((entry) => (
                <option key={entry.pk} value={entry.pk}>
                  {entry.name}
                </option>
              ))}
            </Form.Select>
          </Form.Group>
        </Col>
        <Col md={3}>
          <Form.Group controlId="register-grade">
            <Form.Label>Grade</Form.Label>
            <Form.Select value={grade} onChange={(event) => narrow(setGrade)(event.target.value === '' ? '' : Number(event.target.value))}>
              <option value="">Any grade</option>
              {grades.map((entry) => (
                <option key={entry.pk} value={entry.pk}>
                  {entry.name}
                </option>
              ))}
            </Form.Select>
          </Form.Group>
        </Col>
        <Col md={3}>
          <Form.Group controlId="register-container">
            <Form.Label>Container</Form.Label>
            <Form.Select value={container} onChange={(event) => narrow(setContainer)(event.target.value === '' ? '' : Number(event.target.value))}>
              <option value="">Any container</option>
              {containers.map((entry) => (
                <option key={entry.pk} value={entry.pk}>
                  {entry.name} {entry.container_size_label}
                </option>
              ))}
            </Form.Select>
          </Form.Group>
        </Col>
        <Col md={3}>
          <Form.Group controlId="register-ready-from">
            <Form.Label>Expected ready from</Form.Label>
            <Form.Control type="date" value={readyFrom} onChange={(event) => narrow(setReadyFrom)(event.target.value)} />
          </Form.Group>
        </Col>
        <Col md={3}>
          <Form.Group controlId="register-ready-to">
            <Form.Label>Expected ready to</Form.Label>
            <Form.Control type="date" value={readyTo} onChange={(event) => narrow(setReadyTo)(event.target.value)} />
          </Form.Group>
        </Col>
        <Col md={3} className="d-flex align-items-end">
          <Form.Check label="Overdue at stage" checked={stageOverdue} onChange={(event) => narrow(setStageOverdue)(event.target.checked)} />
        </Col>
        <Col md={3}>
          <Form.Label>Quarantine</Form.Label>
          <Form.Select
            value={quarantined === undefined ? '' : String(quarantined)}
            onChange={(event) => narrow(setQuarantined)(event.target.value === '' ? undefined : event.target.value === 'true')}
          >
            <option value="">Any status</option>
            <option value="true">Quarantined</option>
            <option value="false">Not quarantined</option>
          </Form.Select>
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
      {(selection.mode === 'filter' || selection.ids.length > 0) && (
        <BulkOperationPanel selection={selection} filters={filters} locations={locations} setSelection={setSelection} />
      )}

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
