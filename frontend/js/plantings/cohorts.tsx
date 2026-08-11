import React from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Alert, Badge, Button, Card, Col, Form, Row, Table } from 'react-bootstrap'
import { Link } from 'react-router'

import { getLocations } from '../api/locations'
import { getInventoryItems } from '../api/inventory'
import {
  getCohort,
  getCohortAvailability,
  getCohorts,
  getGrowthStages,
  getPlantGrades,
  getProductionBatches,
  mergeCohorts,
  observeCohort,
  postCohortAction
} from '../api/plantings'
import { queryClient, queryKeys } from '../query'
import { CohortAction, CohortFilters, CohortLifecycleState, PlantCohort } from '../types/plantings'

const STATE_LABELS: Record<CohortLifecycleState, string> = {
  growing: 'Growing',
  available: 'Available',
  retained: 'Retained',
  depleted: 'Depleted'
}

function CohortTotals({ filters }: { filters: CohortFilters }) {
  const { data } = useQuery({
    queryKey: queryKeys.plantings.cohortAvailability(filters),
    queryFn: ({ signal }) => getCohortAvailability(filters, signal)
  })
  if (!data) return null
  return (
    <Row className="g-2 mb-3">
      {[
        ['Available cohorts', data.cohort_quantity],
        ['Available identified plants', data.individual_count],
        ['Combined availability', data.combined_total]
      ].map(([label, value]) => (
        <Col key={label} md={4}>
          <Card body className="py-2">
            <div className="text-muted small">{label}</div>
            <div className="fs-5">{value}</div>
          </Card>
        </Col>
      ))}
    </Row>
  )
}

function ObserveCohortForm() {
  const [batch, setBatch] = React.useState<number | ''>('')
  const [quantity, setQuantity] = React.useState(1)
  const [location, setLocation] = React.useState<number | ''>('')
  const [notes, setNotes] = React.useState('')
  const { data: batches = [] } = useQuery({
    queryKey: queryKeys.plantings.batches('active', '', '', false),
    queryFn: ({ signal }) => getProductionBatches({ status: 'active' }, signal)
  })
  const { data: locations = [] } = useQuery({
    queryKey: queryKeys.locations.list('active'),
    queryFn: ({ signal }) => getLocations(signal, true)
  })
  const mutation = useMutation({
    mutationFn: observeCohort,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.plantings.cohortsAll })
      setQuantity(1)
      setNotes('')
    }
  })
  return (
    <Card body className="mb-3">
      <h2 className="h5">Observe a cohort</h2>
      <Form
        onSubmit={(event) => {
          event.preventDefault()
          if (batch === '') return
          mutation.mutate({
            batch,
            quantity,
            location: location === '' ? null : location,
            notes,
            idempotency_key: crypto.randomUUID()
          })
        }}
      >
        <Row className="g-2 align-items-end">
          <Col md={4}>
            <Form.Label>Production batch</Form.Label>
            <Form.Select value={batch} required onChange={(event) => setBatch(event.target.value === '' ? '' : Number(event.target.value))}>
              <option value="">Select batch</option>
              {batches.map((entry) => (
                <option key={entry.pk} value={entry.pk}>
                  {entry.code}
                </option>
              ))}
            </Form.Select>
          </Col>
          <Col md={2}>
            <Form.Label>Count</Form.Label>
            <Form.Control type="number" min={1} value={quantity} onChange={(event) => setQuantity(Number(event.target.value))} />
          </Col>
          <Col md={3}>
            <Form.Label>Location</Form.Label>
            <Form.Select value={location} onChange={(event) => setLocation(event.target.value === '' ? '' : Number(event.target.value))}>
              <option value="">Not placed</option>
              {locations.map((entry) => (
                <option key={entry.pk} value={entry.pk}>
                  {entry.full_name}
                </option>
              ))}
            </Form.Select>
          </Col>
          <Col md={3}>
            <Form.Label>Notes</Form.Label>
            <Form.Control value={notes} onChange={(event) => setNotes(event.target.value)} />
          </Col>
          <Col>
            <Button type="submit" disabled={mutation.isPending || batch === ''}>
              Record cohort
            </Button>
          </Col>
        </Row>
      </Form>
    </Card>
  )
}

function CohortRegisterView() {
  const [search, setSearch] = React.useState('')
  const [state, setState] = React.useState<CohortLifecycleState | ''>('')
  const [stage, setStage] = React.useState<number | ''>('')
  const [grade, setGrade] = React.useState<number | ''>('')
  const [container, setContainer] = React.useState<number | ''>('')
  const [readyFrom, setReadyFrom] = React.useState('')
  const [readyTo, setReadyTo] = React.useState('')
  const [stageOverdue, setStageOverdue] = React.useState(false)
  const [page, setPage] = React.useState(1)
  const [selected, setSelected] = React.useState<Array<number>>([])
  const [mergeReason, setMergeReason] = React.useState('')
  const filters: CohortFilters = {
    search: search || undefined,
    state: state || undefined,
    stage: stage === '' ? undefined : stage,
    grade: grade === '' ? undefined : grade,
    container: container === '' ? undefined : container,
    expected_ready_from: readyFrom || undefined,
    expected_ready_to: readyTo || undefined,
    stage_overdue: stageOverdue || undefined,
    page,
    page_size: 50
  }
  const { data: stages = [] } = useQuery({ queryKey: ['growth-stages'], queryFn: ({ signal }) => getGrowthStages(signal) })
  const { data: grades = [] } = useQuery({ queryKey: ['plant-grades'], queryFn: ({ signal }) => getPlantGrades(signal) })
  const { data: containers = [] } = useQuery({
    queryKey: ['inventory', 'pot-containers'],
    queryFn: ({ signal }) => getInventoryItems({ category: 'pot_container' }, signal)
  })
  const { data, isPending } = useQuery({
    queryKey: queryKeys.plantings.cohorts(filters),
    queryFn: ({ signal }) => getCohorts(filters, signal)
  })
  const mergeMutation = useMutation({
    mutationFn: mergeCohorts,
    onSuccess: () => {
      setSelected([])
      setMergeReason('')
      void queryClient.invalidateQueries({ queryKey: queryKeys.plantings.cohortsAll })
    }
  })
  return (
    <main className="container py-3">
      <h1>Cohort inventory</h1>
      <p>Homogeneous nursery stock tracked by quantity until individual identity is useful.</p>
      <CohortTotals filters={{ search: filters.search }} />
      <ObserveCohortForm />
      <Row className="g-2 mb-3">
        <Col md={4}>
          <Form.Control type="search" placeholder="Batch code" value={search} onChange={(event) => setSearch(event.target.value)} />
        </Col>
        <Col md={3}>
          <Form.Select value={state} onChange={(event) => setState(event.target.value as CohortLifecycleState | '')}>
            <option value="">All states</option>
            {Object.entries(STATE_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </Form.Select>
        </Col>
        <Col md={3}>
          <Form.Select value={stage} aria-label="Growth stage" onChange={(event) => setStage(event.target.value === '' ? '' : Number(event.target.value))}>
            <option value="">All growth stages</option>
            {stages.map((entry) => (
              <option key={entry.pk} value={entry.pk}>
                {entry.name}
              </option>
            ))}
          </Form.Select>
        </Col>
        <Col md={3}>
          <Form.Select value={grade} aria-label="Plant grade" onChange={(event) => setGrade(event.target.value === '' ? '' : Number(event.target.value))}>
            <option value="">All grades</option>
            {grades.map((entry) => (
              <option key={entry.pk} value={entry.pk}>
                {entry.name}
              </option>
            ))}
          </Form.Select>
        </Col>
        <Col md={3}>
          <Form.Select value={container} aria-label="Container" onChange={(event) => setContainer(event.target.value === '' ? '' : Number(event.target.value))}>
            <option value="">All containers</option>
            {containers.map((entry) => (
              <option key={entry.pk} value={entry.pk}>
                {entry.name} {entry.container_size_label}
              </option>
            ))}
          </Form.Select>
        </Col>
        <Col md={2}>
          <Form.Control type="date" aria-label="Expected ready from" value={readyFrom} onChange={(event) => setReadyFrom(event.target.value)} />
        </Col>
        <Col md={2}>
          <Form.Control type="date" aria-label="Expected ready to" value={readyTo} onChange={(event) => setReadyTo(event.target.value)} />
        </Col>
        <Col md="auto">
          <Form.Check label="Overdue at stage" checked={stageOverdue} onChange={(event) => setStageOverdue(event.target.checked)} />
        </Col>
      </Row>
      {selected.length >= 2 && (
        <Alert variant="light" className="d-flex gap-2 align-items-end">
          <div className="flex-grow-1">
            <strong>Merge {selected.length} cohorts</strong>
            <div className="small text-muted">Cohort #{selected[0]} will keep the identity. Compatibility is rechecked when applied.</div>
            <Form.Control className="mt-2" placeholder="Reason for merge" value={mergeReason} onChange={(event) => setMergeReason(event.target.value)} />
          </div>
          <Button
            disabled={!mergeReason.trim() || mergeMutation.isPending}
            onClick={() => {
              const rows = data?.results.filter((entry) => selected.includes(entry.pk)) ?? []
              mergeMutation.mutate({
                target: selected[0],
                sources: selected.slice(1),
                revisions: Object.fromEntries(rows.map((entry) => [String(entry.pk), entry.revision])),
                reason: mergeReason,
                idempotency_key: crypto.randomUUID()
              })
            }}
          >
            Merge
          </Button>
        </Alert>
      )}
      {isPending ? (
        <div>Loading cohorts…</div>
      ) : (
        <>
          <Table responsive hover>
            <thead>
              <tr>
                <th aria-label="Select" />
                <th>Cohort</th>
                <th>Crop</th>
                <th>Batch</th>
                <th>State</th>
                <th>Quantity</th>
                <th>Stage / grade</th>
                <th>Container</th>
                <th>Location</th>
                <th>Label</th>
              </tr>
            </thead>
            <tbody>
              {data?.results.map((cohort) => (
                <tr key={cohort.pk}>
                  <td>
                    <Form.Check
                      aria-label={`Select cohort ${cohort.pk}`}
                      disabled={cohort.quantity === 0}
                      checked={selected.includes(cohort.pk)}
                      onChange={(event) => setSelected(event.target.checked ? [...selected, cohort.pk] : selected.filter((pk) => pk !== cohort.pk))}
                    />
                  </td>
                  <td>
                    <Link to={`/plantings/cohorts/${cohort.pk}`}>#{cohort.pk}</Link>
                  </td>
                  <td>
                    {cohort.plant_name} — {cohort.variety_name}
                  </td>
                  <td>{cohort.batch_code}</td>
                  <td>
                    <Badge bg={cohort.lifecycle_state === 'available' ? 'success' : 'secondary'}>{STATE_LABELS[cohort.lifecycle_state]}</Badge>
                  </td>
                  <td>{cohort.quantity}</td>
                  <td>
                    {cohort.stage_name ?? '—'}
                    <div className="text-muted small">{cohort.grade_name ?? 'Ungraded'}</div>
                  </td>
                  <td>
                    {cohort.container_name ?? '—'}
                    {cohort.container !== null && (
                      <div className="text-muted small">
                        {cohort.container_size || 'Size not set'} × {cohort.container_count}
                      </div>
                    )}
                  </td>
                  <td>{cohort.location_name || 'Not placed'}</td>
                  <td>{cohort.label_code}</td>
                </tr>
              ))}
            </tbody>
          </Table>
          <div className="d-flex gap-2">
            <Button variant="outline-secondary" disabled={!data?.previous} onClick={() => setPage(Math.max(1, page - 1))}>
              Previous
            </Button>
            <Button variant="outline-secondary" disabled={!data?.next} onClick={() => setPage(page + 1)}>
              Next
            </Button>
          </div>
        </>
      )}
    </main>
  )
}

function CohortActionPanel({ cohort }: { cohort: PlantCohort }) {
  const [actionName, setActionName] = React.useState('adjust')
  const [quantity, setQuantity] = React.useState(cohort.quantity)
  const [reason, setReason] = React.useState('')
  const [location, setLocation] = React.useState<number | ''>(cohort.location ?? '')
  const [containerCount, setContainerCount] = React.useState<number | ''>('')
  const [message, setMessage] = React.useState('')
  const mutation = useMutation({
    mutationFn: (payload: CohortAction) => postCohortAction(cohort.pk, actionName, payload),
    onSuccess: (result) => {
      const promoted = 'plants' in result ? ` Promoted plants: ${result.plants.join(', ')}.` : ''
      setMessage(`Operation applied.${promoted}`)
      void queryClient.invalidateQueries({ queryKey: queryKeys.plantings.cohortsAll })
      void queryClient.invalidateQueries({ queryKey: queryKeys.plantings.cohort(cohort.pk) })
    }
  })
  const { data: locations = [] } = useQuery({
    queryKey: queryKeys.locations.list('active'),
    queryFn: ({ signal }) => getLocations(signal, true)
  })
  const needsQuantity = ['adjust', 'loss', 'split', 'promote'].includes(actionName)
  return (
    <Card body className="mb-3">
      <h2 className="h5">Review an operation</h2>
      {message && <Alert variant="success">{message}</Alert>}
      <Form
        onSubmit={(event) => {
          event.preventDefault()
          mutation.mutate({
            expected_revision: cohort.revision,
            idempotency_key: crypto.randomUUID(),
            quantity: needsQuantity ? quantity : undefined,
            container_count: cohort.container !== null && ['split', 'promote'].includes(actionName) && containerCount !== '' ? containerCount : undefined,
            location: actionName === 'move' && location !== '' ? location : undefined,
            reason
          })
        }}
      >
        <Row className="g-2 align-items-end">
          <Col md={3}>
            <Form.Label>Action</Form.Label>
            <Form.Select value={actionName} onChange={(event) => setActionName(event.target.value)}>
              <option value="adjust">Reconcile count</option>
              <option value="split">Split cohort</option>
              <option value="loss">Record loss</option>
              <option value="promote">Promote to plant IDs</option>
              <option value="ready">Mark available</option>
              <option value="retain">Retain</option>
              <option value="move">Move</option>
            </Form.Select>
          </Col>
          {needsQuantity && (
            <Col md={2}>
              <Form.Label>Quantity</Form.Label>
              <Form.Control type="number" min={actionName === 'adjust' ? 0 : 1} value={quantity} onChange={(event) => setQuantity(Number(event.target.value))} />
            </Col>
          )}
          {cohort.container !== null && ['split', 'promote'].includes(actionName) && (
            <Col md={2}>
              <Form.Label>Containers allocated</Form.Label>
              <Form.Control
                required
                type="number"
                min={1}
                value={containerCount}
                onChange={(event) => setContainerCount(event.target.value === '' ? '' : Number(event.target.value))}
              />
            </Col>
          )}
          {actionName === 'move' && (
            <Col md={3}>
              <Form.Label>Destination</Form.Label>
              <Form.Select value={location} required onChange={(event) => setLocation(event.target.value === '' ? '' : Number(event.target.value))}>
                <option value="">Select location</option>
                {locations.map((entry) => (
                  <option key={entry.pk} value={entry.pk}>
                    {entry.full_name}
                  </option>
                ))}
              </Form.Select>
            </Col>
          )}
          <Col md={5}>
            <Form.Label>Reason</Form.Label>
            <Form.Control value={reason} onChange={(event) => setReason(event.target.value)} />
          </Col>
          <Col md={2}>
            <Button type="submit" disabled={mutation.isPending || cohort.quantity === 0}>
              Apply
            </Button>
          </Col>
          <Col md={2}>
            <Card body>
              <div className="text-muted small">Stage / grade</div>
              <div>{cohort.stage_name ?? 'Not recorded'}</div>
              <div className="small">{cohort.grade_name ?? 'Ungraded'}</div>
            </Card>
          </Col>
          <Col md={2}>
            <Card body>
              <div className="text-muted small">Container</div>
              <div>{cohort.container_name ?? 'Not recorded'}</div>
              {cohort.container !== null && (
                <div className="small">
                  {cohort.container_size || 'Size not set'} × {cohort.container_count}
                </div>
              )}
            </Card>
          </Col>
        </Row>
      </Form>
    </Card>
  )
}

function CohortDetailView({ cohortPk }: { cohortPk: number }) {
  const { data: cohort, isPending } = useQuery({
    queryKey: queryKeys.plantings.cohort(cohortPk),
    queryFn: ({ signal }) => getCohort(cohortPk, signal)
  })
  if (isPending) return <main className="container py-3">Loading cohort…</main>
  if (!cohort) return <main className="container py-3">Cohort not found.</main>
  return (
    <main className="container py-3">
      <Link to="/plantings/cohorts">← Cohort inventory</Link>
      <h1>Cohort #{cohort.pk}</h1>
      <p>
        {cohort.plant_name} — {cohort.variety_name}, batch {cohort.batch_code}
      </p>
      <Row className="g-2 mb-3">
        <Col md={3}>
          <Card body>
            <div className="text-muted">Quantity</div>
            <div className="fs-4">{cohort.quantity}</div>
          </Card>
        </Col>
        <Col md={3}>
          <Card body>
            <div className="text-muted">State</div>
            <div>{STATE_LABELS[cohort.lifecycle_state]}</div>
          </Card>
        </Col>
        <Col md={3}>
          <Card body>
            <div className="text-muted">Location</div>
            <div>{cohort.location_name || 'Not placed'}</div>
          </Card>
        </Col>
        <Col md={3}>
          <Card body>
            <div className="text-muted">Label</div>
            <div>{cohort.label_code}</div>
          </Card>
        </Col>
        <Col md={3}>
          <Card body>
            <div className="text-muted">Production cost</div>
            <div>{cohort.cost === null ? 'Unknown' : `${cohort.currency_code} ${cohort.cost}`}</div>
          </Card>
        </Col>
      </Row>
      <CohortActionPanel cohort={cohort} />
      <h2>History</h2>
      <Table responsive>
        <thead>
          <tr>
            <th>When</th>
            <th>Action</th>
            <th>Quantity</th>
            <th>State</th>
            <th>Reason</th>
          </tr>
        </thead>
        <tbody>
          {cohort.events?.map((event) => (
            <tr key={event.pk}>
              <td>{new Date(event.occurred_at).toLocaleString()}</td>
              <td>{event.action}</td>
              <td>
                {event.quantity_before} → {event.quantity_after}
              </td>
              <td>
                {STATE_LABELS[event.state_before]} → {STATE_LABELS[event.state_after]}
              </td>
              <td>{event.reason}</td>
            </tr>
          ))}
        </tbody>
      </Table>
      {(cohort.promoted_plants?.length ?? 0) > 0 && (
        <p>
          Promoted plants:{' '}
          {cohort.promoted_plants?.map((plantPk) => (
            <Link key={plantPk} className="me-2" to={`/plantings/plants/${plantPk}`}>
              #{plantPk}
            </Link>
          ))}
        </p>
      )}
    </main>
  )
}

export { CohortDetailView, CohortRegisterView }
