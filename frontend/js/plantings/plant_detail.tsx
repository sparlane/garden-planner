import React from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Card, Col, Row, Table } from 'react-bootstrap'
import { NavLink } from 'react-router'

import { AttachmentGallery, AttachmentUploader } from '../attachments'
import { getPlantCostBreakdown } from '../api/costing'
import { getSpecificPlant, getSpecificPlantLifecycleEvents } from '../api/plantings'
import { queryKeys } from '../query'
import { PlantCostBreakdown } from '../types/costing'
import { SpecificPlantLocation } from '../types/plantings'
import { Workspace } from '../types/workspace'
import { formatDate, formatDateTime, formatMoney } from '../utils'
import { EVENT_LABELS, LifecycleStateBadge, PlantAvailabilitySpans, PlantLifecycleHistory } from './lifecycle'

function locationLabel(location: SpecificPlantLocation): string {
  if (location.location_type === 'seed_tray_cell') {
    return `Seed tray cell #${location.seed_tray_cell}`
  }
  return `Garden square #${location.garden_square}`
}

// Physical history in the order it happened, which is separate from the
// lifecycle history beside it: where a plant has been and what became of it
// are different facts and are never merged into one timeline.
function LocationHistory({ locations }: { locations: Array<SpecificPlantLocation> }) {
  if (locations.length === 0) {
    return <p className="text-muted mb-0">No location has been recorded.</p>
  }
  return (
    <Table size="sm" className="mb-0">
      <thead>
        <tr>
          <th>Where</th>
          <th>From</th>
          <th>Until</th>
        </tr>
      </thead>
      <tbody>
        {locations.map((location) => (
          <tr key={location.pk}>
            <td>{locationLabel(location)}</td>
            <td>{formatDateTime(location.started)}</td>
            <td>{location.ended === undefined || location.ended === null ? <span className="text-success">Still there</span> : formatDateTime(location.ended)}</td>
          </tr>
        ))}
      </tbody>
    </Table>
  )
}

// A provisional figure and a final one mean different things, so exactly one of
// them carries a number and they are never added together.
function PlantCost({ breakdown }: { breakdown: PlantCostBreakdown }) {
  return (
    <>
      <dl className="row mb-2">
        <dt className="col-sm-5">Provisional value</dt>
        <dd className="col-sm-7">{formatMoney(breakdown.provisional_value, breakdown.currency_code, 'Final')}</dd>
        <dt className="col-sm-5">Final value</dt>
        <dd className="col-sm-7">{formatMoney(breakdown.final_value, breakdown.currency_code, 'Still provisional')}</dd>
      </dl>
      {breakdown.unknown_cost && <p className="text-muted small">Some inputs reaching this plant have no recorded unit cost, so this figure is incomplete.</p>}
      {breakdown.layers.length === 0 ? (
        <p className="text-muted mb-0">No cost has reached this plant yet.</p>
      ) : (
        <Table size="sm" className="mb-0">
          <thead>
            <tr>
              <th>Item</th>
              <th>Lot</th>
              <th>Amount</th>
            </tr>
          </thead>
          <tbody>
            {breakdown.layers.map((layer) => (
              <tr key={layer.allocation}>
                <td>{layer.item === null ? '—' : `#${layer.item}`}</td>
                <td>{layer.lot === null ? '—' : `#${layer.lot}`}</td>
                <td>{formatMoney(layer.amount, layer.currency_code, 'Unknown')}</td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </>
  )
}

interface PlantDetailViewProps {
  plantPk: number
  workspace: Workspace
}

function PlantDetailView({ plantPk, workspace }: PlantDetailViewProps) {
  const queryClient = useQueryClient()
  const { data: plant, isPending } = useQuery({
    queryKey: queryKeys.plantings.specificPlantDetail(plantPk),
    queryFn: ({ signal }) => getSpecificPlant(plantPk, signal)
  })
  const { data: events = [] } = useQuery({
    queryKey: queryKeys.plantings.plantLifecycle(plantPk),
    queryFn: ({ signal }) => getSpecificPlantLifecycleEvents(plantPk, signal)
  })
  // Cost is a nursery question. A Garden workspace has the same subledger but
  // no reason to read a per-seedling valuation, so it is not fetched at all.
  const { data: cost } = useQuery({
    queryKey: queryKeys.costing.plant(plantPk),
    queryFn: ({ signal }) => getPlantCostBreakdown(plantPk, signal),
    enabled: workspace.mode === 'nursery'
  })

  if (isPending) {
    return <main className="container py-3">Loading plant…</main>
  }
  if (plant === undefined) {
    return <main className="container py-3">Plant not found.</main>
  }

  const refreshPlant = () => queryClient.invalidateQueries({ queryKey: queryKeys.plantings.specificPlantDetail(plantPk) })

  return (
    <main className="container py-3">
      <h1>
        Plant #{plant.pk} <LifecycleStateBadge state={plant.lifecycle_state} />
      </h1>
      <p>
        Raised in batch <NavLink to={`/plantings/batches/${plant.batch}`}>#{plant.batch}</NavLink>, germinated {formatDateTime(plant.germinated)}.
      </p>

      <Row className="g-3">
        {workspace.mode === 'nursery' && (
          <Col md={6}>
            <Card>
              <Card.Header>Nursery growth</Card.Header>
              <Card.Body>
                <dl className="row mb-2">
                  <dt className="col-sm-5">Stage</dt>
                  <dd className="col-sm-7">{plant.growth.stage_name ?? 'Not recorded'}</dd>
                  <dt className="col-sm-5">Grade</dt>
                  <dd className="col-sm-7">{plant.growth.grade_name ?? 'Not graded'}</dd>
                  <dt className="col-sm-5">Container</dt>
                  <dd className="col-sm-7">
                    {plant.growth.container_name === null
                      ? 'Not recorded'
                      : `${plant.growth.container_name} ${plant.growth.container_size ?? ''} × ${plant.growth.container_count}`}
                  </dd>
                  <dt className="col-sm-5">Expected ready</dt>
                  <dd className="col-sm-7">{plant.growth.expected_ready === null ? 'Not recorded' : formatDate(plant.growth.expected_ready)}</dd>
                  <dt className="col-sm-5">Measurements</dt>
                  <dd className="col-sm-7">
                    {plant.growth.height_cm ?? '—'} cm high / {plant.growth.spread_cm ?? '—'} cm spread
                  </dd>
                  <dt className="col-sm-5">Roots</dt>
                  <dd className="col-sm-7">{plant.growth.root_condition || 'Not recorded'}</dd>
                </dl>
                {plant.nursery_observations.length > 0 && (
                  <Table size="sm">
                    <thead>
                      <tr>
                        <th>When</th>
                        <th>Observation</th>
                      </tr>
                    </thead>
                    <tbody>
                      {plant.nursery_observations.map((entry) => (
                        <tr key={entry.pk}>
                          <td>{formatDateTime(entry.occurred_at)}</td>
                          <td>
                            {[entry.stage_name, entry.grade_name, entry.container_name, entry.notes].filter(Boolean).join(' · ')}
                            {entry.photo_url && (
                              <div>
                                <a href={entry.photo_url} target="_blank" rel="noreferrer">
                                  View photo
                                </a>
                              </div>
                            )}
                            <AttachmentGallery attachments={entry.attachments} />
                            <details className="mt-2">
                              <summary>Add observation photos</summary>
                              <AttachmentUploader
                                id={`nursery-observation-photos-${entry.pk}`}
                                targetType="nursery_observation"
                                targetId={entry.pk}
                                onUploaded={() => void refreshPlant()}
                              />
                            </details>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </Table>
                )}
              </Card.Body>
            </Card>
          </Col>
        )}
        <Col md={6}>
          <Card>
            <Card.Header>Photos</Card.Header>
            <Card.Body>
              <AttachmentGallery attachments={plant.attachments} />
              {plant.attachments.length === 0 && <p className="text-muted">No photos attached yet.</p>}
              <AttachmentUploader id="plant-photos" targetType="plant" targetId={plant.pk} onUploaded={() => void refreshPlant()} />
            </Card.Body>
          </Card>
        </Col>
        <Col md={6}>
          <Card>
            <Card.Header>Lineage</Card.Header>
            <Card.Body>
              <dl className="row mb-0">
                <dt className="col-sm-5">Batch</dt>
                <dd className="col-sm-7">
                  <NavLink to={`/plantings/batches/${plant.batch}`}>#{plant.batch}</NavLink>
                </dd>
                <dt className="col-sm-5">Cell allocation</dt>
                <dd className="col-sm-7">#{plant.cell_planting}</dd>
                <dt className="col-sm-5">Germinated</dt>
                <dd className="col-sm-7">{formatDateTime(plant.germinated)}</dd>
                <dt className="col-sm-5">Offerable</dt>
                <dd className="col-sm-7">{plant.sellable ? 'Yes' : 'No'}</dd>
                <dt className="col-sm-5">Sales allocation</dt>
                <dd className="col-sm-7">
                  {plant.allocation_status === 'none' ? 'Free' : plant.allocation_status === 'tentative' ? 'Tentatively claimed' : 'Reserved'}
                  {plant.allocation_orders.map((claim) => (
                    <div key={`${claim.order}:${claim.status}`}>
                      <NavLink to={`/sales/orders/${claim.order}`}>{claim.order_number}</NavLink> · {claim.status === 'pending' ? 'tentative' : 'reserved'}
                    </div>
                  ))}
                </dd>
                <dt className="col-sm-5">In this state since</dt>
                <dd className="col-sm-7">{plant.state_since === null ? 'Not recorded' : formatDateTime(plant.state_since)}</dd>
                <dt className="col-sm-5">Final outcome</dt>
                <dd className="col-sm-7">
                  {plant.final_outcome === null ? 'Not resolved' : `${EVENT_LABELS[plant.final_outcome]} on ${formatDateTime(plant.final_outcome_at ?? '')}`}
                </dd>
              </dl>
              {plant.notes && <p className="mt-2 mb-0">{plant.notes}</p>}
            </Card.Body>
          </Card>
        </Col>
        <Col md={6}>
          <Card>
            <Card.Header>Where it has been</Card.Header>
            <Card.Body>
              <LocationHistory locations={plant.locations} />
            </Card.Body>
          </Card>
        </Col>
        <Col md={6}>
          <Card>
            <Card.Header>Lifecycle history</Card.Header>
            <Card.Body>
              <PlantLifecycleHistory events={events} />
            </Card.Body>
          </Card>
        </Col>
        <Col md={6}>
          <Card>
            <Card.Header>When it was offered</Card.Header>
            <Card.Body>
              <PlantAvailabilitySpans intervals={plant.availability_intervals} />
            </Card.Body>
          </Card>
        </Col>
        {workspace.mode === 'nursery' && (
          <Col md={6}>
            <Card>
              <Card.Header>What it has cost</Card.Header>
              <Card.Body>{cost === undefined ? <div className="text-muted">Loading cost…</div> : <PlantCost breakdown={cost} />}</Card.Body>
            </Card>
          </Col>
        )}
      </Row>
    </main>
  )
}

export { PlantDetailView }
