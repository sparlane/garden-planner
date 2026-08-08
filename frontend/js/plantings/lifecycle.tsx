import React from 'react'
import { Badge, Button, Table } from 'react-bootstrap'

import { formatDateTime } from '../utils'
import { PlantLifecycleEvent, PlantLifecycleEventType, PlantLifecycleState, PlantOutcomeAction, SpecificPlant } from '../types/plantings'

const STATE_LABELS: Record<PlantLifecycleState, string> = {
  growing: 'Growing',
  available: 'Available',
  retained: 'Retained',
  donated: 'Donated',
  failed: 'Failed',
  culled: 'Culled',
  harvested: 'Harvested'
}

const STATE_VARIANTS: Record<PlantLifecycleState, string> = {
  growing: 'secondary',
  available: 'success',
  retained: 'primary',
  donated: 'info',
  failed: 'danger',
  culled: 'dark',
  harvested: 'primary'
}

const EVENT_LABELS: Record<PlantLifecycleEventType, string> = {
  germinated: 'Germinated',
  ready: 'Ready for sale or use',
  transplanted: 'Planted out',
  retained: 'Retained',
  failed: 'Failed',
  culled: 'Culled',
  donated: 'Donated',
  harvest_finished: 'Harvest finished',
  corrected: 'Corrected'
}

// Actions offered on a plant that has not yet been resolved. `ready` is only
// meaningful while a plant is still growing, so it is filtered out below.
const OUTCOME_ACTIONS: Array<{ action: PlantOutcomeAction; label: string; variant: string }> = [
  { action: 'ready', label: 'Ready', variant: 'outline-success' },
  { action: 'retain', label: 'Retain', variant: 'outline-primary' },
  { action: 'finish-harvest', label: 'Harvested', variant: 'outline-primary' },
  { action: 'fail', label: 'Failed', variant: 'outline-danger' },
  { action: 'cull', label: 'Cull', variant: 'outline-danger' },
  { action: 'donate', label: 'Donate', variant: 'outline-info' }
]

function LifecycleStateBadge({ state }: { state: PlantLifecycleState }) {
  return <Badge bg={STATE_VARIANTS[state]}>{STATE_LABELS[state]}</Badge>
}

function PlantLifecycleBadge({ plant }: { plant: SpecificPlant }) {
  return <LifecycleStateBadge state={plant.lifecycle_state} />
}

function availableActions(plant: SpecificPlant): Array<{ action: PlantOutcomeAction; label: string; variant: string }> {
  if (plant.final_outcome !== null) {
    // A retained plant is finished for availability but still growing, so it
    // can still fail or be harvested out.
    if (plant.lifecycle_state !== 'retained') {
      return []
    }
    return OUTCOME_ACTIONS.filter((entry) => entry.action === 'fail' || entry.action === 'cull' || entry.action === 'finish-harvest')
  }
  if (plant.lifecycle_state === 'available') {
    return OUTCOME_ACTIONS.filter((entry) => entry.action !== 'ready')
  }
  return OUTCOME_ACTIONS
}

interface PlantOutcomeButtonsProps {
  plant: SpecificPlant
  onOutcome: (plant: SpecificPlant, outcome: PlantOutcomeAction) => void
  disabled?: boolean
}

function PlantOutcomeButtons({ plant, onOutcome, disabled }: PlantOutcomeButtonsProps) {
  const actions = availableActions(plant)
  if (actions.length === 0) {
    return <span className="text-muted">Resolved</span>
  }
  return (
    <div className="d-flex flex-wrap gap-1">
      {actions.map((entry) => (
        <Button key={entry.action} size="sm" variant={entry.variant} disabled={disabled} onClick={() => onOutcome(plant, entry.action)}>
          {entry.label}
        </Button>
      ))}
    </div>
  )
}

interface PlantLifecycleHistoryProps {
  events: Array<PlantLifecycleEvent>
  onReverse?: (event: PlantLifecycleEvent) => void
}

function PlantLifecycleHistory({ events, onReverse }: PlantLifecycleHistoryProps) {
  if (events.length === 0) {
    return <p className="text-muted mb-0">No lifecycle history recorded.</p>
  }
  return (
    <Table size="sm" className="mb-0">
      <thead>
        <tr>
          <th>When</th>
          <th>Event</th>
          <th>Reason</th>
          {onReverse && <th />}
        </tr>
      </thead>
      <tbody>
        {events.map((event) => (
          <tr key={event.pk} className={event.reversed_by === null ? undefined : 'text-muted text-decoration-line-through'}>
            <td>{formatDateTime(event.occurred_at)}</td>
            <td>{EVENT_LABELS[event.event_type]}</td>
            <td>{event.reason || '—'}</td>
            {onReverse && (
              <td>
                {event.reversed_by === null && event.event_type !== 'germinated' && event.event_type !== 'corrected' && (
                  <Button size="sm" variant="outline-secondary" onClick={() => onReverse(event)}>
                    Correct
                  </Button>
                )}
              </td>
            )}
          </tr>
        ))}
      </tbody>
    </Table>
  )
}

export { EVENT_LABELS, STATE_LABELS, LifecycleStateBadge, PlantLifecycleBadge, PlantLifecycleHistory, PlantOutcomeButtons }
