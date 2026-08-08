import React from 'react'
import { useQuery } from '@tanstack/react-query'
import { Card, Col, Row, Table } from 'react-bootstrap'
import { NavLink } from 'react-router'

import { getPlantCostBreakdown } from '../api/costing'
import { getSpecificPlant, getSpecificPlantLifecycleEvents } from '../api/plantings'
import { queryKeys } from '../query'
import { PlantCostBreakdown } from '../types/costing'
import { SpecificPlantLocation } from '../types/plantings'
import { Workspace } from '../types/workspace'
import { formatDateTime, formatMoney } from '../utils'
import { LifecycleStateBadge, PlantLifecycleHistory } from './lifecycle'

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

  return (
    <main className="container py-3">
      <h1>
        Plant #{plant.pk} <LifecycleStateBadge state={plant.lifecycle_state} />
      </h1>
      <p>
        Raised in batch <NavLink to={`/plantings/batches/${plant.batch}`}>#{plant.batch}</NavLink>, germinated {formatDateTime(plant.germinated)}.
      </p>

      <Row className="g-3">
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
                <dt className="col-sm-5">Final outcome</dt>
                <dd className="col-sm-7">{plant.final_outcome === null ? 'Not resolved' : `${plant.final_outcome} on ${formatDateTime(plant.final_outcome_at ?? '')}`}</dd>
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
