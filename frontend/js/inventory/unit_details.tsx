import React from 'react'
import { useQuery } from '@tanstack/react-query'
import { Badge, Card, Col, Row, Table } from 'react-bootstrap'
import { Link } from 'react-router'

import { getSerializedUnit } from '../api/inventory'
import { getLabelIdentities } from '../api/labels'
import { queryKeys } from '../query'
import { SerializedPhysicalState } from '../types/inventory'
import { formatMoney } from '../utils'

const STATE_LABELS: Record<SerializedPhysicalState, string> = {
  available: 'On hand',
  quarantined: 'Quarantined',
  lost: 'Lost',
  retired: 'Retired',
  dispatched: 'Sold and dispatched',
  returned: 'Returned'
}

const STATE_VARIANTS: Record<SerializedPhysicalState, string> = {
  available: 'success',
  quarantined: 'warning',
  lost: 'danger',
  retired: 'secondary',
  dispatched: 'info',
  returned: 'info'
}

interface NumberedUnitDetailsProps {
  unitPk: number
}

// Where a scanned container's code lands. It answers the three questions
// somebody standing in front of it has — what is this, where is it meant to
// be, and is it still ours — before any of the history.
function NumberedUnitDetails({ unitPk }: NumberedUnitDetailsProps) {
  const { data: unit, isPending } = useQuery({
    queryKey: queryKeys.inventory.serializedUnit(unitPk),
    queryFn: ({ signal }) => getSerializedUnit(unitPk, signal)
  })
  // The code lives with the label identity rather than on the unit, so that a
  // replaced code changes in one place. Only this container's row is used.
  const { data: identities = [] } = useQuery({
    queryKey: ['labels', 'identities', 'inventoryunit'],
    queryFn: ({ signal }) => getLabelIdentities(signal)
  })
  const identity = identities.find((row) => row.target_type === 'inventoryunit' && row.object_id === unitPk)

  if (isPending) {
    return <main className="container-fluid mt-3">Loading container…</main>
  }
  if (!unit) {
    return <main className="container-fluid mt-3">Container not found.</main>
  }

  return (
    <main className="container-fluid mt-3">
      <h1 className="h4">
        {unit.item_name} <span className="text-muted">{unit.asset_code}</span>
      </h1>
      <Row className="g-3">
        <Col md={6}>
          <Card>
            <Card.Body>
              <Card.Title>Where it is</Card.Title>
              <Table borderless size="sm" className="mb-0">
                <tbody>
                  <tr>
                    <th scope="row">State</th>
                    <td>
                      <Badge bg={STATE_VARIANTS[unit.physical_state]}>{STATE_LABELS[unit.physical_state]}</Badge>
                    </td>
                  </tr>
                  <tr>
                    <th scope="row">Location</th>
                    <td>{unit.current_location_full_name ?? <span className="text-muted">Not on hand</span>}</td>
                  </tr>
                  <tr>
                    <th scope="row">Holding a plant</th>
                    <td>{unit.in_use ? 'Yes' : 'No'}</td>
                  </tr>
                  <tr>
                    <th scope="row">Label code</th>
                    <td>{identity ? identity.code : <span className="text-muted">Not issued</span>}</td>
                  </tr>
                </tbody>
              </Table>
            </Card.Body>
          </Card>
        </Col>
        <Col md={6}>
          <Card>
            <Card.Body>
              <Card.Title>Where it came from</Card.Title>
              <Table borderless size="sm" className="mb-0">
                <tbody>
                  <tr>
                    <th scope="row">Item</th>
                    <td>
                      <Link to="/inventory">{unit.item_name}</Link>
                    </td>
                  </tr>
                  <tr>
                    <th scope="row">Lot</th>
                    <td>{unit.source_lot}</td>
                  </tr>
                  <tr>
                    <th scope="row">Cost</th>
                    <td>{formatMoney(unit.acquisition_cost, unit.currency_code, 'Not recorded')}</td>
                  </tr>
                  <tr>
                    <th scope="row">Numbered on</th>
                    <td>{new Date(unit.created).toLocaleDateString()}</td>
                  </tr>
                </tbody>
              </Table>
            </Card.Body>
          </Card>
        </Col>
      </Row>
    </main>
  )
}

export { NumberedUnitDetails }
