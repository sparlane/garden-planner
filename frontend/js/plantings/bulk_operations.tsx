import React from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Card, Col, Form, Row, Table } from 'react-bootstrap'

import { getGardenSquares } from '../api/garden'
import { getNurseryRegisterSelection, postBulkPlantOperation, previewBulkPlantOperation } from '../api/plantings'
import { getSeedTrayCells, getSeedTrays } from '../api/seedtrays'
import { queryKeys } from '../query'
import { Location } from '../types/locations'
import { BulkPlantAction, BulkPlantAtomicity, BulkPlantOperationRequest, BulkPlantPreview, NurseryRegisterFilters } from '../types/plantings'
import { localDatetimeInputValue, parseLocalDatetimeInput } from '../utils'
import { EMPTY_SELECTION, RegisterSelection } from './register_list'

const ACTIONS: Array<{ value: BulkPlantAction; label: string }> = [
  { value: 'move', label: 'Move or transplant' },
  { value: 'ready', label: 'Mark ready' },
  { value: 'retain', label: 'Retain' },
  { value: 'donate', label: 'Donate' },
  { value: 'fail', label: 'Record failed' },
  { value: 'cull', label: 'Cull' },
  { value: 'finish_harvest', label: 'Finish harvest' }
]

type DestinationType = 'location' | 'garden_square' | 'seed_tray_cell'

interface BulkOperationPanelProps {
  selection: RegisterSelection
  filters: NurseryRegisterFilters
  locations: Array<Location>
  setSelection: (selection: RegisterSelection) => void
}

function BulkOperationPanel({ selection, filters, locations, setSelection }: BulkOperationPanelProps) {
  const cache = useQueryClient()
  const [action, setAction] = React.useState<BulkPlantAction>('move')
  const [atomicity, setAtomicity] = React.useState<BulkPlantAtomicity | ''>('')
  const [occurredAt, setOccurredAt] = React.useState(localDatetimeInputValue())
  const [reason, setReason] = React.useState('')
  const [destinationType, setDestinationType] = React.useState<DestinationType>('location')
  const [destination, setDestination] = React.useState<number | ''>('')
  const [tray, setTray] = React.useState<number | ''>('')
  const [overrideReason, setOverrideReason] = React.useState('')
  const [request, setRequest] = React.useState<BulkPlantOperationRequest>()
  const [preview, setPreview] = React.useState<BulkPlantPreview>()

  const gardenSquaresQuery = useQuery({ queryKey: queryKeys.garden.squares, queryFn: ({ signal }) => getGardenSquares(signal) })
  const traysQuery = useQuery({ queryKey: queryKeys.seedTrays.trays, queryFn: ({ signal }) => getSeedTrays(signal) })
  const cellsQuery = useQuery({
    queryKey: queryKeys.seedTrays.cells(tray || 0),
    queryFn: ({ signal }) => getSeedTrayCells(tray as number, signal),
    enabled: destinationType === 'seed_tray_cell' && tray !== ''
  })

  function invalidateReview() {
    setPreview(undefined)
    setRequest(undefined)
  }

  function actionPayload(): Record<string, unknown> {
    if (action !== 'move') return {}
    if (destinationType === 'location') {
      return { location_type: destinationType, location: destination, override_reason: overrideReason }
    }
    if (destinationType === 'garden_square') {
      return { location_type: destinationType, garden_square: destination }
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
      selection_source: selection.mode === 'filter' ? { mode: 'filter', filters } : { mode: 'ids' },
      action_payload: actionPayload()
    }
    previewMutation.mutate(reviewedRequest)
  }

  const moveIncomplete = action === 'move' && destination === ''
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
                  invalidateReview()
                }}
              >
                <option value="location">Nursery location</option>
                <option value="garden_square">Garden square</option>
                <option value="seed_tray_cell">Seed tray cell</option>
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

        <Button className="mt-3" variant="outline-primary" disabled={moveIncomplete || previewMutation.isPending} onClick={review}>
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
                      <td>{row.before.lifecycle_state}</td>
                      <td>
                        {row.after.lifecycle_state}
                        {row.after.location_type ? ` at ${row.after.location_type.replaceAll('_', ' ')}` : ''}
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
