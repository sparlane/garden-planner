import React from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Badge, Button, Card, Col, Form, Row, Table } from 'react-bootstrap'
import { Link } from 'react-router'

import { addProductionBatch, getProductionBatch, getProductionBatches, postProductionBatchAction, updateProductionBatch } from '../api/plantings'
import { getPlantVarieties } from '../api/plants'
import { queryKeys } from '../query'
import { formatDate, formatDateTime } from '../utils'
import { ProductionBatch, ProductionBatchDetail, ProductionBatchStatus } from '../types/plantings'

const STATUS_LABELS: Record<ProductionBatchStatus, string> = {
  planned: 'Planned',
  active: 'Active',
  output_finalized: 'Output finalized',
  completed: 'Completed',
  cancelled: 'Cancelled'
}

const STATUS_VARIANTS: Record<ProductionBatchStatus, string> = {
  planned: 'secondary',
  active: 'success',
  output_finalized: 'info',
  completed: 'primary',
  cancelled: 'dark'
}

function BatchStatusBadge({ status }: { status: ProductionBatchStatus }) {
  return <Badge bg={STATUS_VARIANTS[status]}>{STATUS_LABELS[status]}</Badge>
}

interface NewBatchFormProps {
  done: () => void
}

function NewBatchForm({ done }: NewBatchFormProps) {
  const queryClient = useQueryClient()
  const [code, setCode] = React.useState('')
  const [variety, setVariety] = React.useState<number>()
  const [plannedStart, setPlannedStart] = React.useState('')
  const [notes, setNotes] = React.useState('')
  const { data: varieties = [] } = useQuery({
    queryKey: queryKeys.plants.varieties,
    queryFn: ({ signal }) => getPlantVarieties(signal)
  })
  const mutation = useMutation({
    mutationFn: addProductionBatch,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.plantings.batchesAll })
      done()
    }
  })

  function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (variety === undefined) {
      return
    }
    mutation.mutate({
      code,
      variety,
      planned_start: plannedStart || null,
      notes
    })
  }

  return (
    <Card className="mb-3">
      <Card.Body>
        <Card.Title>New batch</Card.Title>
        <Form onSubmit={submit}>
          <Row className="g-2 align-items-end">
            <Col md={3}>
              <Form.Group controlId="batch-code">
                <Form.Label>Batch code</Form.Label>
                <Form.Control required maxLength={64} value={code} onChange={(event) => setCode(event.target.value)} />
              </Form.Group>
            </Col>
            <Col md={3}>
              <Form.Group controlId="batch-variety">
                <Form.Label>Variety</Form.Label>
                <Form.Select required value={variety ?? ''} onChange={(event) => setVariety(event.target.value ? Number(event.target.value) : undefined)}>
                  <option value="">Choose a variety…</option>
                  {varieties.map((option) => (
                    <option key={option.pk} value={option.pk}>
                      {option.name}
                    </option>
                  ))}
                </Form.Select>
              </Form.Group>
            </Col>
            <Col md={3}>
              <Form.Group controlId="batch-planned-start">
                <Form.Label>Planned start</Form.Label>
                <Form.Control type="date" value={plannedStart} onChange={(event) => setPlannedStart(event.target.value)} />
              </Form.Group>
            </Col>
            <Col md={3}>
              <Form.Group controlId="batch-notes">
                <Form.Label>Notes</Form.Label>
                <Form.Control value={notes} onChange={(event) => setNotes(event.target.value)} />
              </Form.Group>
            </Col>
          </Row>
          <div className="mt-3">
            <Button type="submit" disabled={mutation.isPending}>
              {mutation.isPending ? 'Creating…' : 'Create batch'}
            </Button>
            <Button className="ms-2" variant="outline-secondary" onClick={done}>
              Cancel
            </Button>
          </div>
        </Form>
      </Card.Body>
    </Card>
  )
}

function ProductionBatchTable() {
  const [showAdd, setShowAdd] = React.useState(false)
  const [status, setStatus] = React.useState<ProductionBatchStatus | ''>('')
  const [code, setCode] = React.useState('')
  const [needsRepair, setNeedsRepair] = React.useState(false)
  const { data: batches = [], isPending } = useQuery({
    queryKey: queryKeys.plantings.batches(status, '', code, needsRepair),
    queryFn: ({ signal }) => getProductionBatches({ status, code, needsRepair }, signal)
  })

  return (
    <main className="container py-3">
      <h1>Production batches</h1>
      <p>A batch is the shared cultivation identity for one tracked crop: its sowings, plants, and lifecycle.</p>
      <div className="mb-3">
        <Button onClick={() => setShowAdd(true)} disabled={showAdd}>
          Add batch
        </Button>
      </div>
      {showAdd && <NewBatchForm done={() => setShowAdd(false)} />}
      <Row className="g-2 mb-3">
        <Col md={3}>
          <Form.Select aria-label="Filter by status" value={status} onChange={(event) => setStatus(event.target.value as ProductionBatchStatus | '')}>
            <option value="">All statuses</option>
            {Object.entries(STATUS_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </Form.Select>
        </Col>
        <Col md={3}>
          <Form.Control placeholder="Search codes" value={code} onChange={(event) => setCode(event.target.value)} />
        </Col>
        <Col md={3} className="d-flex align-items-center">
          <Form.Check id="batch-needs-repair" label="Needs repair" checked={needsRepair} onChange={(event) => setNeedsRepair(event.target.checked)} />
        </Col>
      </Row>
      {isPending ? (
        <div>Loading batches…</div>
      ) : (
        <Table responsive>
          <thead>
            <tr>
              <th>Code</th>
              <th>Crop</th>
              <th>Status</th>
              <th>Started</th>
              <th>Sowings</th>
              <th>Seeds / clusters sown</th>
              <th>Plants observed</th>
              <th>Currently placed</th>
              <th>Final outcomes</th>
            </tr>
          </thead>
          <tbody>
            {batches.map((batch) => (
              <tr key={batch.pk}>
                <td>
                  <Link to={`/plantings/batches/${batch.pk}`}>{batch.code}</Link>
                  {batch.repair_state === 'needs_repair' && (
                    <Badge className="ms-2" bg="warning" text="dark">
                      Needs repair
                    </Badge>
                  )}
                </td>
                <td>
                  {batch.plant_name} - {batch.variety_name}
                </td>
                <td>
                  <BatchStatusBadge status={batch.status} />
                </td>
                <td>{batch.actual_start ? formatDate(batch.actual_start) : formatDate(batch.planned_start ?? '')}</td>
                <td>{batch.sowing_count}</td>
                <td>{batch.seeds_sown}</td>
                <td>{batch.plants_observed}</td>
                <td>{batch.plants_with_active_location}</td>
                <td>{batch.final_outcomes}</td>
              </tr>
            ))}
            {batches.length === 0 && (
              <tr>
                <td colSpan={9}>No batches match these filters.</td>
              </tr>
            )}
          </tbody>
        </Table>
      )}
    </main>
  )
}

interface BatchActionsProps {
  batch: ProductionBatchDetail
}

function BatchActions({ batch }: BatchActionsProps) {
  const queryClient = useQueryClient()
  const [reason, setReason] = React.useState('')
  const [error, setError] = React.useState<string>()
  const mutation = useMutation({
    mutationFn: ({ batchAction, data }: { batchAction: string; data: { reason?: string } }) => postProductionBatchAction(batch.pk, batchAction, data),
    onSuccess: () => {
      setReason('')
      setError(undefined)
      return Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.plantings.batch(batch.pk) }),
        queryClient.invalidateQueries({ queryKey: queryKeys.plantings.batchesAll })
      ])
    },
    onError: (caught: unknown) => setError(caught instanceof Error ? caught.message : String(caught))
  })

  function run(batchAction: string) {
    setError(undefined)
    mutation.mutate({ batchAction, data: { reason } })
  }

  const canActivate = batch.status === 'planned'
  const canFinalize = batch.status === 'active'
  const canComplete = batch.status === 'output_finalized'
  const canCancel = batch.status === 'planned' || batch.status === 'active'
  const canReopen = batch.status === 'output_finalized' || batch.status === 'completed' || batch.status === 'cancelled'

  return (
    <Card className="mb-3">
      <Card.Body>
        <Card.Title>Lifecycle</Card.Title>
        <Form.Group className="mb-3" controlId="batch-action-reason">
          <Form.Label>Reason</Form.Label>
          <Form.Control value={reason} onChange={(event) => setReason(event.target.value)} placeholder="Required to cancel or reopen" />
        </Form.Group>
        <div className="d-flex flex-wrap gap-2">
          <Button disabled={!canActivate || mutation.isPending} onClick={() => run('activate')}>
            Activate
          </Button>
          <Button disabled={!canFinalize || mutation.isPending} onClick={() => run('finalize-output')}>
            Finalize output
          </Button>
          <Button disabled={!canComplete || mutation.isPending} onClick={() => run('complete')}>
            Complete
          </Button>
          <Button variant="outline-danger" disabled={!canCancel || !reason.trim() || mutation.isPending} onClick={() => run('cancel')}>
            Cancel batch
          </Button>
          <Button variant="outline-secondary" disabled={!canReopen || !reason.trim() || mutation.isPending} onClick={() => run('reopen')}>
            Reopen
          </Button>
        </div>
        {error && (
          <Alert className="mt-3" variant="danger">
            {error}
          </Alert>
        )}
        {batch.unresolved_plants.length > 0 && batch.status === 'output_finalized' && (
          <Alert className="mt-3" variant="warning">
            {batch.unresolved_plants.length} observed plants have no final disposition yet, so this batch cannot complete.
          </Alert>
        )}
      </Card.Body>
    </Card>
  )
}

function BatchSummary({ batch }: { batch: ProductionBatchDetail }) {
  const entries: Array<[string, string | number]> = [
    ['Status', STATUS_LABELS[batch.status]],
    ['Planned start', batch.planned_start ? formatDate(batch.planned_start) : '—'],
    ['Actual start', batch.actual_start ? formatDateTime(batch.actual_start) : '—'],
    ['Output finalized', batch.output_finalized_at ? formatDateTime(batch.output_finalized_at) : '—'],
    ['Completed', batch.completed_at ? formatDateTime(batch.completed_at) : '—'],
    ['Cancelled', batch.cancelled_at ? formatDateTime(batch.cancelled_at) : '—'],
    ['Seeds / clusters sown', batch.seeds_sown],
    ['Plants observed', batch.plants_observed],
    ['Plants with an active location', batch.plants_with_active_location],
    ['Final outcomes', batch.final_outcomes]
  ]

  return (
    <Card className="mb-3">
      <Card.Body>
        <Card.Title>Summary</Card.Title>
        <Table size="sm" borderless>
          <tbody>
            {entries.map(([label, value]) => (
              <tr key={label}>
                <th scope="row" className="fw-normal text-muted">
                  {label}
                </th>
                <td>{value}</td>
              </tr>
            ))}
          </tbody>
        </Table>
      </Card.Body>
    </Card>
  )
}

function BatchSowings({ batch }: { batch: ProductionBatchDetail }) {
  return (
    <Card className="mb-3">
      <Card.Body>
        <Card.Title>Sowings</Card.Title>
        {batch.sowings.length === 0 && <div>No sowings have joined this batch yet.</div>}
        {batch.sowings.map((sowing) => (
          <div key={`${sowing.sowing_type}-${sowing.pk}`} className="mb-3">
            <div>
              <strong>
                {sowing.sowing_type} #{sowing.pk}
              </strong>{' '}
              — {sowing.quantity} sown {formatDateTime(sowing.planted)}
              {sowing.removed && (
                <Badge className="ms-2" bg="secondary">
                  Closed
                </Badge>
              )}
            </div>
            <div className="text-muted">
              Packet #{sowing.seeds_used}
              {sowing.seed_lot !== null && <> · lot #{sowing.seed_lot}</>}
              {sowing.seed_tray !== null && (
                <>
                  {' '}
                  · <Link to={`/seedtrays/${sowing.seed_tray}`}>tray #{sowing.seed_tray}</Link>
                </>
              )}
              {sowing.location && <> · {sowing.location}</>}
            </div>
            {sowing.cells.length > 0 && (
              <Table size="sm" className="mt-2">
                <thead>
                  <tr>
                    <th>Cell</th>
                    <th>Position</th>
                    <th>Sown</th>
                    <th>Plants observed</th>
                  </tr>
                </thead>
                <tbody>
                  {sowing.cells.map((cell) => (
                    <tr key={cell.pk}>
                      <td>#{cell.cell}</td>
                      <td>
                        {cell.x_position}, {cell.y_position}
                      </td>
                      <td>{cell.quantity}</td>
                      <td>{cell.plants_observed}</td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            )}
          </div>
        ))}
      </Card.Body>
    </Card>
  )
}

function BatchLocations({ batch }: { batch: ProductionBatchDetail }) {
  return (
    <Card className="mb-3">
      <Card.Body>
        <Card.Title>Current locations</Card.Title>
        {batch.current_locations.length === 0 ? (
          <div>No individual plants from this batch currently occupy a tracked location.</div>
        ) : (
          <Table size="sm">
            <thead>
              <tr>
                <th>Plant</th>
                <th>Location</th>
                <th>Since</th>
              </tr>
            </thead>
            <tbody>
              {batch.current_locations.map((location) => (
                <tr key={`${location.specific_plant}-${location.started}`}>
                  <td>#{location.specific_plant}</td>
                  <td>{location.label}</td>
                  <td>{formatDateTime(location.started)}</td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card.Body>
    </Card>
  )
}

function BatchHistory({ batch }: { batch: ProductionBatchDetail }) {
  return (
    <Card className="mb-3">
      <Card.Body>
        <Card.Title>Lifecycle history</Card.Title>
        <Table size="sm">
          <thead>
            <tr>
              <th>When</th>
              <th>Change</th>
              <th>Reason</th>
            </tr>
          </thead>
          <tbody>
            {batch.transitions.map((transition) => (
              <tr key={transition.pk}>
                <td>{formatDateTime(transition.created)}</td>
                <td>
                  {transition.previous_status ? STATUS_LABELS[transition.previous_status] : 'Created'} → {STATUS_LABELS[transition.new_status]}
                </td>
                <td>{transition.reason || '—'}</td>
              </tr>
            ))}
          </tbody>
        </Table>
      </Card.Body>
    </Card>
  )
}

interface BatchDetailsFormProps {
  batch: ProductionBatch
}

function BatchDetailsForm({ batch }: BatchDetailsFormProps) {
  const queryClient = useQueryClient()
  const [code, setCode] = React.useState(batch.code)
  const [plannedStart, setPlannedStart] = React.useState(batch.planned_start ?? '')
  const [notes, setNotes] = React.useState(batch.notes)
  const mutation = useMutation({
    mutationFn: () => updateProductionBatch(batch.pk, { code, planned_start: plannedStart || null, notes }),
    onSuccess: () =>
      Promise.all([queryClient.invalidateQueries({ queryKey: queryKeys.plantings.batch(batch.pk) }), queryClient.invalidateQueries({ queryKey: queryKeys.plantings.batchesAll })])
  })

  React.useEffect(() => {
    setCode(batch.code)
    setPlannedStart(batch.planned_start ?? '')
    setNotes(batch.notes)
  }, [batch])

  function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    mutation.mutate()
  }

  return (
    <Card className="mb-3">
      <Card.Body>
        <Card.Title>Details</Card.Title>
        <Form onSubmit={submit}>
          <Row className="g-2">
            <Col md={4}>
              <Form.Group controlId="batch-detail-code">
                <Form.Label>Batch code</Form.Label>
                <Form.Control required maxLength={64} value={code} onChange={(event) => setCode(event.target.value)} />
              </Form.Group>
            </Col>
            <Col md={4}>
              <Form.Group controlId="batch-detail-planned-start">
                <Form.Label>Planned start</Form.Label>
                <Form.Control type="date" value={plannedStart} onChange={(event) => setPlannedStart(event.target.value)} />
              </Form.Group>
            </Col>
            <Col md={4}>
              <Form.Group controlId="batch-detail-notes">
                <Form.Label>Notes</Form.Label>
                <Form.Control value={notes} onChange={(event) => setNotes(event.target.value)} />
              </Form.Group>
            </Col>
          </Row>
          <Button className="mt-3" type="submit" disabled={mutation.isPending}>
            {mutation.isPending ? 'Saving…' : 'Save details'}
          </Button>
          {mutation.isSuccess && <span className="ms-3 text-success">Details saved.</span>}
        </Form>
      </Card.Body>
    </Card>
  )
}

interface ProductionBatchDetailViewProps {
  batchPk: number
}

function ProductionBatchDetailView({ batchPk }: ProductionBatchDetailViewProps) {
  const { data: batch, isPending } = useQuery({
    queryKey: queryKeys.plantings.batch(batchPk),
    queryFn: ({ signal }) => getProductionBatch(batchPk, signal)
  })

  if (isPending) {
    return <div className="container py-3">Loading batch…</div>
  }
  if (!batch) {
    return <div className="container py-3">Batch not found.</div>
  }

  return (
    <main className="container py-3">
      <div className="d-flex align-items-center gap-3">
        <h1 className="mb-0">{batch.code}</h1>
        <BatchStatusBadge status={batch.status} />
      </div>
      <p className="text-muted">
        {batch.plant_name} - {batch.variety_name}
      </p>
      <Link to="/plantings/batches">← All batches</Link>
      {batch.repair_state === 'needs_repair' && (
        <Alert className="mt-3" variant="warning">
          <Alert.Heading>This migrated batch needs repair</Alert.Heading>
          <pre className="mb-0 text-wrap">{batch.repair_details}</pre>
        </Alert>
      )}
      <div className="mt-3">
        <BatchSummary batch={batch} />
        <BatchActions batch={batch} />
        <BatchDetailsForm batch={batch} />
        <BatchSowings batch={batch} />
        <BatchLocations batch={batch} />
        <BatchHistory batch={batch} />
      </div>
    </main>
  )
}

export { ProductionBatchTable, ProductionBatchDetailView, STATUS_LABELS }
