import React from 'react'
import { Badge, Form, Table } from 'react-bootstrap'
import { NavLink } from 'react-router'

import { formatDate, formatDateTime, formatHoldRemaining, formatMoney } from '../utils'
import { NurseryRegisterRow } from '../types/plantings'
import { LifecycleStateBadge } from './lifecycle'

// A selection is either the plants an operator ticked, or the filter itself.
// Storing the filter rather than the IDs it currently matches means a bulk
// action acts on what is true when it runs, not on what was on screen when
// the box was ticked.
type RegisterSelection = { mode: 'ids'; ids: Array<number> } | { mode: 'filter' }

const EMPTY_SELECTION: RegisterSelection = { mode: 'ids', ids: [] }

function isSelected(selection: RegisterSelection, plantPk: number): boolean {
  return selection.mode === 'filter' || selection.ids.includes(plantPk)
}

function toggleSelected(selection: RegisterSelection, plantPk: number): RegisterSelection {
  // Unticking one row out of "everything matching" leaves the operator with no
  // honest way to describe what is left, so it is refused rather than guessed.
  if (selection.mode === 'filter') {
    return selection
  }
  if (selection.ids.includes(plantPk)) {
    return { mode: 'ids', ids: selection.ids.filter((entry) => entry !== plantPk) }
  }
  return { mode: 'ids', ids: [...selection.ids, plantPk] }
}

// A tray is named by its number first and its model second. The model alone
// names every tray of that kind at once, which is no help to somebody trying
// to find the one plant they are looking at.
function LocationCell({ row }: { row: NurseryRegisterRow }) {
  if (row.location_type === null) {
    return <span className="text-muted">Not placed</span>
  }
  return (
    <>
      {row.location_type === 'seed_tray_cell' ? (
        <>
          Tray #{row.seed_tray}
          <div className="text-muted small">{row.location_label}</div>
        </>
      ) : (
        row.location_label
      )}
      {row.located_since !== null && <div className="text-muted small">since {formatDate(row.located_since)}</div>}
    </>
  )
}

// Where the plant is physically standing, which for a plant in a tray is
// wherever the tray has been wheeled. Shown beside the location rather than
// instead of it: "cell B4 of tray 12" and "Bench A" are both true, and an
// operator walking the greenhouse needs the second to find the first.
function StandingAtCell({ row }: { row: NurseryRegisterRow }) {
  if (row.standing_at === null) {
    return <span className="text-muted">—</span>
  }
  return <>{row.standing_at_label}</>
}

function ReadyCell({ row }: { row: NurseryRegisterRow }) {
  if (row.expected_ready === null) {
    return <span className="text-muted">Unknown</span>
  }
  return <span className={row.stage_overdue ? 'text-danger' : ''}>{formatDate(row.expected_ready)}</span>
}

interface RegisterTableProps {
  rows: Array<NurseryRegisterRow>
  selection: RegisterSelection
  setSelection: (selection: RegisterSelection) => void
}

function RegisterTable({ rows, selection, setSelection }: RegisterTableProps) {
  if (rows.length === 0) {
    return <p className="text-muted">No plants match these filters.</p>
  }
  return (
    <Table striped hover responsive size="sm">
      <thead>
        <tr>
          <th />
          <th>Plant</th>
          <th>Crop</th>
          <th>Batch</th>
          <th>State</th>
          <th>Stage / grade</th>
          <th>Container</th>
          <th>Age</th>
          <th>Expected ready</th>
          <th>Where</th>
          <th>Standing in</th>
          <th>Cost</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.pk}>
            <td>
              <Form.Check
                aria-label={`Select plant ${row.pk}`}
                checked={isSelected(selection, row.pk)}
                disabled={selection.mode === 'filter' || row.quarantined || row.reserved}
                onChange={() => setSelection(toggleSelected(selection, row.pk))}
              />
            </td>
            <td>
              <NavLink to={`/plantings/plants/${row.pk}`}>#{row.pk}</NavLink>
            </td>
            <td>
              {row.variety_name}
              <div className="text-muted small">{row.plant_name}</div>
            </td>
            <td>
              <NavLink to={`/plantings/batches/${row.batch}`}>{row.batch_code}</NavLink>
            </td>
            <td>
              <LifecycleStateBadge state={row.lifecycle_state} />
              {row.state_since !== null && <div className="text-muted small">since {formatDate(row.state_since)}</div>}
              {row.quarantined && (
                <div>
                  <Badge bg="warning" text="dark">
                    Quarantined · unavailable
                  </Badge>
                </div>
              )}
              {row.reserved && (
                <div>
                  <Badge bg="primary">Reserved · unavailable</Badge>
                  <div className="text-muted small">Hold {formatHoldRemaining(row.reserved_until)}</div>
                </div>
              )}
              {row.allocation_status === 'tentative' && (
                <div>
                  <Badge bg="warning" text="dark">
                    Tentatively claimed · still available
                  </Badge>
                </div>
              )}
              {row.allocation_orders.map((claim) => (
                <div className="small" key={`${claim.order}:${claim.status}`}>
                  <NavLink to={`/sales/orders/${claim.order}`}>{claim.order_number}</NavLink> · {claim.status === 'pending' ? 'tentative' : 'reserved'}
                </div>
              ))}
            </td>
            <td>
              {row.stage_name ?? '—'}
              {row.stage_overdue && <div className="text-danger small">Overdue at stage</div>}
              <div className="text-muted small">{row.grade_name ?? 'Ungraded'}</div>
            </td>
            <td>
              {row.container_name ?? '—'}
              {row.container !== null && (
                <div className="text-muted small">
                  {row.container_size || 'Size not set'} × {row.container_count}
                </div>
              )}
            </td>
            <td>
              {row.age_days} days
              <div className="text-muted small">{formatDateTime(row.germinated)}</div>
            </td>
            <td>
              <ReadyCell row={row} />
            </td>
            <td>
              <LocationCell row={row} />
            </td>
            <td>
              <StandingAtCell row={row} />
            </td>
            <td>{formatMoney(row.cost, row.currency_code, 'Not costed')}</td>
          </tr>
        ))}
      </tbody>
    </Table>
  )
}

export { EMPTY_SELECTION, RegisterSelection, RegisterTable, isSelected, toggleSelected }
