import React from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Card, Col, Form, Row, Table } from 'react-bootstrap'

import { getGardenSquares } from '../api/garden'
import { getPotFills, PotFill } from '../api/container_fills'
import { getGrowthStages, getNurseryRegisterSelection, getPlantGrades, postBulkPlantOperation, previewBulkPlantOperation } from '../api/plantings'
import { getSeedTrayCells, getSeedTrays } from '../api/seedtrays'
import { activeChoices } from '../catalog'
import { queryKeys } from '../query'
import { Location } from '../types/locations'
import { BulkPlantAction, BulkPlantAtomicity, BulkPlantOperationRequest, BulkPlantPreview, NurseryRegisterFilters } from '../types/plantings'
import { useNowDatetimeInput } from '../utils'
import { STATE_LABELS } from './lifecycle'
import { PLACEMENT_LABELS } from './placements'
import { PotCodeField, potOptionLabel, useNumberedPotDestinations } from './pot_destinations'
import { EMPTY_SELECTION, RegisterSelection } from './register_list'

const ACTIONS: Array<{ value: BulkPlantAction; label: string }> = [
  { value: 'move', label: 'Move or transplant' },
  { value: 'stage', label: 'Update growth stage' },
  { value: 'grade', label: 'Update grade' },
  { value: 'repot', label: 'Pot on or repot' },
  { value: 'ready', label: 'Mark ready' },
  { value: 'hold_back', label: 'Hold back from sale' },
  { value: 'retain', label: 'Retain' },
  { value: 'end_retention', label: 'End retention' },
  { value: 'donate', label: 'Donate' },
  { value: 'fail', label: 'Record failed' },
  { value: 'cull', label: 'Cull' },
  { value: 'finish_harvest', label: 'Finish harvest' }
]

// The places a plant can be put. A numbered pot is reached through the item it
// came from, the way a cell is reached through its tray, which is what
// `pot_destinations.ts` holds the two lists for.
type DestinationType = 'location' | 'garden_square' | 'seed_tray_cell' | 'container_unit'

interface BulkOperationPanelProps {
  selection: RegisterSelection
  filters: NurseryRegisterFilters
  locations: Array<Location>
  setSelection: (selection: RegisterSelection) => void
  sourceLabels?: Array<string>
}

function BulkOperationPanel({ selection, filters, locations, setSelection, sourceLabels }: BulkOperationPanelProps) {
  const cache = useQueryClient()
  const [action, setAction] = React.useState<BulkPlantAction>('move')
  const [atomicity, setAtomicity] = React.useState<BulkPlantAtomicity | ''>('')
  const occurredAt = useNowDatetimeInput()
  const [reason, setReason] = React.useState('')
  const [destinationType, setDestinationType] = React.useState<DestinationType>('location')
  const [destination, setDestination] = React.useState<number | ''>('')
  const [tray, setTray] = React.useState<number | ''>('')
  const [containerItem, setContainerItem] = React.useState<number | ''>('')
  const [overrideReason, setOverrideReason] = React.useState('')
  const [stage, setStage] = React.useState<number | ''>('')
  const [grade, setGrade] = React.useState<number | ''>('')
  const [fill, setFill] = React.useState<PotFill>()
  const [fillPage, setFillPage] = React.useState(1)
  const [request, setRequest] = React.useState<BulkPlantOperationRequest>()
  const [preview, setPreview] = React.useState<BulkPlantPreview>()

  const gardenSquaresQuery = useQuery({ queryKey: queryKeys.garden.squares, queryFn: ({ signal }) => getGardenSquares(signal) })
  const traysQuery = useQuery({ queryKey: queryKeys.seedTrays.trays, queryFn: ({ signal }) => getSeedTrays(signal) })
  const cellsQuery = useQuery({
    queryKey: queryKeys.seedTrays.cells(tray || 0),
    queryFn: ({ signal }) => getSeedTrayCells(tray as number, signal),
    enabled: destinationType === 'seed_tray_cell' && tray !== ''
  })
  const stagesQuery = useQuery({ queryKey: queryKeys.plantings.growthCatalogs.stages, queryFn: ({ signal }) => getGrowthStages(signal) })
  const gradesQuery = useQuery({ queryKey: queryKeys.plantings.growthCatalogs.grades, queryFn: ({ signal }) => getPlantGrades(signal) })
  const fillTarget = { kind: 'counted', status: 'open' } as const
  const fillsQuery = useQuery({
    queryKey: queryKeys.containerFills.list(fillTarget, fillPage),
    queryFn: ({ signal }) => getPotFills(fillTarget, fillPage, signal),
    enabled: action === 'repot'
  })
  const { potItems, pots } = useNumberedPotDestinations({ choosing: destinationType === 'container_unit', item: containerItem })

  function invalidateReview() {
    setPreview(undefined)
    setRequest(undefined)
  }

  function actionPayload(): Record<string, unknown> {
    if (action === 'stage') return { stage, notes: reason }
    if (action === 'grade') return { grade, notes: reason }
    if (action === 'repot') return { container_fill: fill?.pk, override_reason: overrideReason, notes: reason }
    if (action !== 'move') return {}
    if (destinationType === 'location') {
      return { location_type: destinationType, location: destination, override_reason: overrideReason }
    }
    if (destinationType === 'garden_square') {
      return { location_type: destinationType, garden_square: destination }
    }
    if (destinationType === 'container_unit') {
      return { location_type: destinationType, container_unit: destination }
    }
    return { location_type: destinationType, seed_tray_cell: destination }
  }

  const previewMutation = useMutation({
    mutationFn: previewBulkPlantOperation,
    onSuccess: (review, reviewedRequest) => {
      setPreview(review)
      setRequest(reviewedRequest)
    }
  })
  const executeMutation = useMutation({
    mutationFn: postBulkPlantOperation,
    onSuccess: () => {
      setSelection(EMPTY_SELECTION)
      setPreview(undefined)
      setRequest(undefined)
      return Promise.all([
        cache.invalidateQueries({ queryKey: queryKeys.plantings.registerAll }),
        cache.invalidateQueries({ queryKey: queryKeys.plantings.specificPlantsAll }),
        cache.invalidateQueries({ queryKey: queryKeys.plantings.batchesAll }),
        cache.invalidateQueries({ queryKey: queryKeys.locations.all }),
        cache.invalidateQueries({ queryKey: queryKeys.containerFills.all }),
        cache.invalidateQueries({ queryKey: queryKeys.inventory.all }),
        cache.invalidateQueries({ queryKey: queryKeys.costing.all }),
        cache.invalidateQueries({ queryKey: queryKeys.reports.all })
      ])
    }
  })

  async function review() {
    const parsed = occurredAt.instant()
    if (!parsed) return
    const resolved = selection.mode === 'filter' ? await getNurseryRegisterSelection(filters) : { plants: selection.ids, count: selection.ids.length }
    const reviewedRequest: BulkPlantOperationRequest = {
      idempotency_key: globalThis.crypto.randomUUID(),
      action,
      atomicity: atomicity || 'all_or_nothing',
      occurred_at: parsed.toISOString(),
      reason,
      plants: resolved.plants,
      selection_source: sourceLabels ? { mode: 'scan', labels: sourceLabels } : selection.mode === 'filter' ? { mode: 'filter', filters } : { mode: 'ids' },
      action_payload: actionPayload()
    }
    previewMutation.mutate(reviewedRequest)
  }

  const actionIncomplete =
    (action === 'move' && destination === '') || (action === 'stage' && stage === '') || (action === 'grade' && grade === '') || (action === 'repot' && !fill)
  return (
    <Card className="mb-3">
      <Card.Body>
        <Card.Title>Bulk action</Card.Title>
        <Row className="g-2">
          <Col md={3}>
            <Form.Label>Action</Form.Label>
            <Form.Select
              value={action}
              onChange={(event) => {
                setAction(event.target.value as BulkPlantAction)
                if (event.target.value === 'repot') setAtomicity('all_or_nothing')
                invalidateReview()
              }}
            >
              {ACTIONS.map((entry) => (
                <option key={entry.value} value={entry.value}>
                  {entry.label}
                </option>
              ))}
            </Form.Select>
          </Col>
          <Col md={3}>
            <Form.Label>When</Form.Label>
            <Form.Control
              type="datetime-local"
              value={occurredAt.value}
              onChange={(event) => {
                occurredAt.change(event.target.value)
                invalidateReview()
              }}
            />
          </Col>
          <Col md={6}>
            <Form.Label>Reason or notes</Form.Label>
            <Form.Control
              value={reason}
              onChange={(event) => {
                setReason(event.target.value)
                invalidateReview()
              }}
            />
          </Col>
        </Row>

        {action === 'move' && (
          <Row className="g-2 mt-1">
            <Col md={3}>
              <Form.Label>Destination kind</Form.Label>
              <Form.Select
                value={destinationType}
                onChange={(event) => {
                  setDestinationType(event.target.value as DestinationType)
                  setDestination('')
                  setTray('')
                  setContainerItem('')
                  invalidateReview()
                }}
              >
                <option value="location">{PLACEMENT_LABELS.location}</option>
                <option value="garden_square">{PLACEMENT_LABELS.garden_square}</option>
                <option value="seed_tray_cell">{PLACEMENT_LABELS.seed_tray_cell}</option>
                <option value="container_unit">{PLACEMENT_LABELS.container_unit}</option>
              </Form.Select>
            </Col>
            {destinationType === 'seed_tray_cell' && (
              <Col md={3}>
                <Form.Label>Tray</Form.Label>
                <Form.Select
                  value={tray}
                  onChange={(event) => {
                    setTray(event.target.value ? Number(event.target.value) : '')
                    setDestination('')
                    invalidateReview()
                  }}
                >
                  <option value="">Select tray</option>
                  {(traysQuery.data ?? []).map((entry) => (
                    <option key={entry.pk} value={entry.pk}>
                      Tray #{entry.pk}
                    </option>
                  ))}
                </Form.Select>
              </Col>
            )}
            {destinationType === 'container_unit' && (
              <Col md={3}>
                <PotCodeField
                  potItems={potItems}
                  onFound={(pot) => {
                    setContainerItem(pot.item)
                    setDestination(pot.pk)
                    invalidateReview()
                  }}
                />
              </Col>
            )}
            {destinationType === 'container_unit' && (
              <Col md={3}>
                <Form.Label>Container item</Form.Label>
                <Form.Select
                  value={containerItem}
                  onChange={(event) => {
                    setContainerItem(event.target.value ? Number(event.target.value) : '')
                    setDestination('')
                    invalidateReview()
                  }}
                >
                  <option value="">Select container item</option>
                  {potItems.map((entry) => (
                    <option key={entry.pk} value={entry.pk}>
                      {entry.name}
                    </option>
                  ))}
                </Form.Select>
              </Col>
            )}
            <Col md={3}>
              <Form.Label>Destination</Form.Label>
              <Form.Select
                value={destination}
                onChange={(event) => {
                  setDestination(event.target.value ? Number(event.target.value) : '')
                  invalidateReview()
                }}
              >
                <option value="">Select destination</option>
                {destinationType === 'location' &&
                  locations.map((entry) => (
                    <option key={entry.pk} value={entry.pk}>
                      {entry.full_name}
                    </option>
                  ))}
                {destinationType === 'garden_square' &&
                  (gardenSquaresQuery.data ?? []).map((entry) => (
                    <option key={entry.pk} value={entry.pk}>
                      {entry.name}
                    </option>
                  ))}
                {destinationType === 'seed_tray_cell' &&
                  (cellsQuery.data ?? []).map((entry) => (
                    <option key={entry.pk} value={entry.pk}>
                      ({entry.x_position},{entry.y_position})
                    </option>
                  ))}
                {destinationType === 'container_unit' &&
                  pots.map((entry) => (
                    <option key={entry.pk} value={entry.pk}>
                      {potOptionLabel(entry)}
                    </option>
                  ))}
              </Form.Select>
            </Col>
            {destinationType === 'location' && (
              <Col md={3}>
                <Form.Label>Capacity override reason</Form.Label>
                <Form.Control
                  value={overrideReason}
                  onChange={(event) => {
                    setOverrideReason(event.target.value)
                    invalidateReview()
                  }}
                  placeholder="Leave blank to enforce capacity"
                />
              </Col>
            )}
          </Row>
        )}

        {action === 'stage' && (
          <Row className="g-2 mt-1">
            <Col md={4}>
              <Form.Label>New stage</Form.Label>
              <Form.Select
                value={stage}
                onChange={(event) => {
                  setStage(event.target.value ? Number(event.target.value) : '')
                  invalidateReview()
                }}
              >
                <option value="">Select stage</option>
                {activeChoices(stagesQuery.data ?? []).map((entry) => (
                  <option key={entry.pk} value={entry.pk}>
                    {entry.name}
                  </option>
                ))}
              </Form.Select>
            </Col>
          </Row>
        )}
        {action === 'grade' && (
          <Row className="g-2 mt-1">
            <Col md={4}>
              <Form.Label>New grade</Form.Label>
              <Form.Select
                value={grade}
                onChange={(event) => {
                  setGrade(event.target.value ? Number(event.target.value) : '')
                  invalidateReview()
                }}
              >
                <option value="">Select grade</option>
                {activeChoices(gradesQuery.data ?? []).map((entry) => (
                  <option key={entry.pk} value={entry.pk}>
                    {entry.name}
                  </option>
                ))}
              </Form.Select>
            </Col>
          </Row>
        )}
        {action === 'repot' && (
          <div className="mt-3">
            <Form.Label>Counted pot fill</Form.Label>
            <p className="text-muted">Choose a fill opened in Inventory. Each selected plant moves into one unused pot at the fill location.</p>
            {fill && (
              <p>
                Selected: {fill.code} · lot #{fill.stock_lot} · {locations.find((entry) => entry.pk === fill.source_location)?.full_name ?? `location #${fill.source_location}`}
              </p>
            )}
            {fillsQuery.isError && <Alert variant="danger">{fillsQuery.error.message}</Alert>}
            {fillsQuery.isPending && <p>Loading fills…</p>}
            {fillsQuery.data?.results.map((entry) => (
              <Form.Check
                key={entry.pk}
                type="radio"
                name="repot-fill"
                id={`repot-fill-${entry.pk}`}
                checked={fill?.pk === entry.pk}
                label={`${entry.code} · lot #${entry.stock_lot} · ${locations.find((location) => location.pk === entry.source_location)?.full_name ?? `location #${entry.source_location}`} · ${entry.container_count} pots originally filled`}
                onChange={() => {
                  setFill(entry)
                  invalidateReview()
                }}
              />
            ))}
            {fillsQuery.data?.count === 0 && <p>No open counted fills. Open one from the pot lot in Inventory first.</p>}
            <div className="d-flex gap-2 my-2">
              <Button size="sm" variant="outline-secondary" disabled={fillPage === 1 || fillsQuery.isFetching} onClick={() => setFillPage(fillPage - 1)}>
                Previous
              </Button>
              <span>Page {fillPage}</span>
              <Button size="sm" variant="outline-secondary" disabled={!fillsQuery.data?.next || fillsQuery.isFetching} onClick={() => setFillPage(fillPage + 1)}>
                Next
              </Button>
            </div>
            <Form.Label>Capacity override reason (optional)</Form.Label>
            <Form.Control
              value={overrideReason}
              onChange={(event) => {
                setOverrideReason(event.target.value)
                invalidateReview()
              }}
            />
          </div>
        )}

        <Button className="mt-3" variant="outline-primary" disabled={actionIncomplete || previewMutation.isPending} onClick={review}>
          {previewMutation.isPending ? 'Reviewing…' : 'Review changes'}
        </Button>

        {preview && request && (
          <div className="mt-3">
            <Alert variant={preview.conflicts ? 'warning' : 'success'}>
              {preview.eligible} of {preview.selected} plants are eligible; {preview.conflicts} have conflicts.
            </Alert>
            {preview.capacity.length > 0 && (
              <p className="small text-muted">Capacity checked at {preview.capacity.map((entry) => `${entry.used} used / ${entry.capacity} ${entry.basis}`).join(', ')}.</p>
            )}
            {preview.plants.length > 0 && (
              <Table size="sm" responsive>
                <thead>
                  <tr>
                    <th>Plant</th>
                    <th>Current</th>
                    <th>After</th>
                    <th>Review</th>
                  </tr>
                </thead>
                <tbody>
                  {preview.plants.slice(0, 100).map((row) => (
                    <tr key={row.plant}>
                      <td>#{row.plant}</td>
                      <td>{STATE_LABELS[row.before.lifecycle_state]}</td>
                      <td>
                        {STATE_LABELS[row.after.lifecycle_state]}
                        {row.after.location_type ? ` at ${PLACEMENT_LABELS[row.after.location_type].toLowerCase()}` : ''}
                      </td>
                      <td>{row.eligible ? 'Eligible' : row.conflicts.join(' ')}</td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            )}
            {preview.plants.length > 100 && <p className="text-muted small">Showing the first 100 of {preview.plants.length} reviewed plants.</p>}
            <Form.Label>How should conflicts be handled?</Form.Label>
            <div>
              <Form.Check
                inline
                type="radio"
                name="bulk-atomicity"
                label="Apply nothing if any conflict remains"
                checked={atomicity === 'all_or_nothing'}
                onChange={() => setAtomicity('all_or_nothing')}
              />
              <Form.Check
                inline
                type="radio"
                name="bulk-atomicity"
                label="Apply eligible plants only"
                disabled={action === 'repot'}
                checked={atomicity === 'eligible_only'}
                onChange={() => setAtomicity('eligible_only')}
              />
            </div>
            <Button
              className="mt-2"
              disabled={!atomicity || executeMutation.isPending || (atomicity === 'all_or_nothing' && preview.conflicts > 0)}
              onClick={() => executeMutation.mutate({ ...request, atomicity: atomicity as BulkPlantAtomicity })}
            >
              {executeMutation.isPending ? 'Applying…' : 'Confirm bulk action'}
            </Button>
          </div>
        )}
      </Card.Body>
    </Card>
  )
}

export { BulkOperationPanel }
