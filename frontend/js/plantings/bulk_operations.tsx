import React from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Card, Col, Form, Row, Table } from 'react-bootstrap'

import { getGardenSquares } from '../api/garden'
import { getInventoryItems, getSerializedUnits } from '../api/inventory'
import { getGrowthStages, getNurseryRegisterSelection, getPlantGrades, postBulkPlantOperation, previewBulkPlantOperation } from '../api/plantings'
import { getSeedTrayCells, getSeedTrays } from '../api/seedtrays'
import { queryKeys } from '../query'
import { Location } from '../types/locations'
import { BulkPlantAction, BulkPlantAtomicity, BulkPlantOperationRequest, BulkPlantPreview, NurseryRegisterFilters } from '../types/plantings'
import { localDatetimeInputValue, parseLocalDatetimeInput } from '../utils'
import { STATE_LABELS } from './lifecycle'
import { PLACEMENT_LABELS } from './placements'
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
// came from, the way a cell is reached through its tray: the pots of one
// catalog item are what an operator is choosing between, and listing every
// numbered container in the nursery at once would not be.
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
  const [occurredAt, setOccurredAt] = React.useState(localDatetimeInputValue())
  const [reason, setReason] = React.useState('')
  const [destinationType, setDestinationType] = React.useState<DestinationType>('location')
  const [destination, setDestination] = React.useState<number | ''>('')
  const [tray, setTray] = React.useState<number | ''>('')
  const [containerItem, setContainerItem] = React.useState<number | ''>('')
  const [overrideReason, setOverrideReason] = React.useState('')
  const [stage, setStage] = React.useState<number | ''>('')
  const [grade, setGrade] = React.useState<number | ''>('')
  const [container, setContainer] = React.useState<number | ''>('')
  const [containerCount, setContainerCount] = React.useState(1)
  const [sourceLocation, setSourceLocation] = React.useState<number | ''>('')
  const [containerLot, setContainerLot] = React.useState<number | ''>('')
  const [request, setRequest] = React.useState<BulkPlantOperationRequest>()
  const [preview, setPreview] = React.useState<BulkPlantPreview>()

  const gardenSquaresQuery = useQuery({ queryKey: queryKeys.garden.squares, queryFn: ({ signal }) => getGardenSquares(signal) })
  const traysQuery = useQuery({ queryKey: queryKeys.seedTrays.trays, queryFn: ({ signal }) => getSeedTrays(signal) })
  const cellsQuery = useQuery({
    queryKey: queryKeys.seedTrays.cells(tray || 0),
    queryFn: ({ signal }) => getSeedTrayCells(tray as number, signal),
    enabled: destinationType === 'seed_tray_cell' && tray !== ''
  })
  const stagesQuery = useQuery({ queryKey: ['growth-stages'], queryFn: ({ signal }) => getGrowthStages(signal) })
  const gradesQuery = useQuery({ queryKey: ['plant-grades'], queryFn: ({ signal }) => getPlantGrades(signal) })
  const containersQuery = useQuery({
    queryKey: ['inventory', 'pot-containers'],
    queryFn: ({ signal }) => getInventoryItems({ category: 'pot_container', active: true }, signal)
  })
  // Only pots still on hand can be stood in. A sold or wasted one keeps its
  // identity forever, so filtering on `active` alone would offer containers
  // that have left the nursery.
  const containerUnitsQuery = useQuery({
    queryKey: queryKeys.inventory.serializedUnits(containerItem, 'available'),
    queryFn: ({ signal }) => getSerializedUnits({ item: containerItem as number, active: true, physical_state: 'available' }, signal),
    enabled: destinationType === 'container_unit' && containerItem !== ''
  })
  // A pot has to be numbered before a plant can be recorded as standing in it,
  // which is what mixed tracking means; the rest of the pot catalog is bought
  // and consumed by the boxful.
  const numberableContainers = (containersQuery.data ?? []).filter((entry) => entry.tracking_mode === 'mixed')

  function invalidateReview() {
    setPreview(undefined)
    setRequest(undefined)
  }

  function actionPayload(appliedAt: string): Record<string, unknown> {
    if (action === 'stage') return { stage, notes: reason }
    if (action === 'grade') return { grade, notes: reason }
    if (action === 'repot') {
      return {
        container_item: container,
        container_count: containerCount,
        notes: reason,
        application: {
          applied_at: appliedAt,
          source_location: sourceLocation,
          batch: null,
          notes: reason,
          lines: [{ item: container, lot: containerLot, applied_quantity: String(containerCount), unit_code: 'each' }]
        }
      }
    }
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
        cache.invalidateQueries({ queryKey: queryKeys.locations.all })
      ])
    }
  })

  async function review() {
    const parsed = parseLocalDatetimeInput(occurredAt)
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
      action_payload: actionPayload(parsed.toISOString())
    }
    previewMutation.mutate(reviewedRequest)
  }

  const actionIncomplete =
    (action === 'move' && destination === '') ||
    (action === 'stage' && stage === '') ||
    (action === 'grade' && grade === '') ||
    (action === 'repot' && (container === '' || sourceLocation === '' || containerLot === '' || containerCount < 1))
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
              value={occurredAt}
              onChange={(event) => {
                setOccurredAt(event.target.value)
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
                  {numberableContainers.map((entry) => (
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
                  (containerUnitsQuery.data ?? []).map((entry) => (
                    <option key={entry.pk} value={entry.pk}>
                      {entry.asset_code}
                      {entry.current_location_full_name ? ` — ${entry.current_location_full_name}` : ''}
                      {entry.in_use ? ' (already holding a plant)' : ''}
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
                {(stagesQuery.data ?? [])
                  .filter((entry) => entry.active)
                  .map((entry) => (
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
                {(gradesQuery.data ?? [])
                  .filter((entry) => entry.active)
                  .map((entry) => (
                    <option key={entry.pk} value={entry.pk}>
                      {entry.name}
                    </option>
                  ))}
              </Form.Select>
            </Col>
          </Row>
        )}
        {action === 'repot' && (
          <Row className="g-2 mt-1">
            <Col md={3}>
              <Form.Label>Container</Form.Label>
              <Form.Select
                value={container}
                onChange={(event) => {
                  setContainer(event.target.value ? Number(event.target.value) : '')
                  invalidateReview()
                }}
              >
                <option value="">Select container</option>
                {(containersQuery.data ?? []).map((entry) => (
                  <option key={entry.pk} value={entry.pk}>
                    {entry.name} {entry.container_size_label}
                  </option>
                ))}
              </Form.Select>
            </Col>
            <Col md={2}>
              <Form.Label>Container count</Form.Label>
              <Form.Control
                type="number"
                min={1}
                value={containerCount}
                onChange={(event) => {
                  setContainerCount(Number(event.target.value))
                  invalidateReview()
                }}
              />
            </Col>
            <Col md={3}>
              <Form.Label>Stock location</Form.Label>
              <Form.Select
                value={sourceLocation}
                onChange={(event) => {
                  setSourceLocation(event.target.value ? Number(event.target.value) : '')
                  invalidateReview()
                }}
              >
                <option value="">Select location</option>
                {locations.map((entry) => (
                  <option key={entry.pk} value={entry.pk}>
                    {entry.full_name}
                  </option>
                ))}
              </Form.Select>
            </Col>
            <Col md={2}>
              <Form.Label>Container lot ID</Form.Label>
              <Form.Control
                type="number"
                min={1}
                value={containerLot}
                onChange={(event) => {
                  setContainerLot(event.target.value ? Number(event.target.value) : '')
                  invalidateReview()
                }}
              />
            </Col>
          </Row>
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
