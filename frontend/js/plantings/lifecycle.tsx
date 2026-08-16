import React from 'react'
import { Badge, Button, Table } from 'react-bootstrap'

import { formatDateTime } from '../utils'
import { AvailabilityInterval, PlantLifecycleEvent, PlantLifecycleEventType, PlantLifecycleState, PlantOutcomeAction, SpecificPlant } from '../types/plantings'

// Every derived state and recorded fact the server can report, including the
// ones sales and health produce. The Record types make tsc refuse a state or an
// event that nothing here knows how to name.
const STATE_LABELS: Record<PlantLifecycleState, string> = {
  growing: 'Growing',
  available: 'Available',
  retained: 'Retained',
  donated: 'Donated',
  failed: 'Failed',
  lost: 'Lost',
  culled: 'Culled',
  harvested: 'Harvested',
  sold: 'Sold',
  quarantined: 'Returned quarantined',
  discarded: 'Returned discarded'
}

const STATE_VARIANTS: Record<PlantLifecycleState, string> = {
  growing: 'secondary',
  available: 'success',
  retained: 'primary',
  donated: 'info',
  failed: 'danger',
  lost: 'dark',
  culled: 'dark',
  harvested: 'primary',
  sold: 'info',
  quarantined: 'warning',
  discarded: 'dark'
}

const EVENT_LABELS: Record<PlantLifecycleEventType, string> = {
  germinated: 'Germinated',
  ready: 'Ready for sale or use',
  transplanted: 'Planted out',
  retained: 'Retained',
  failed: 'Failed',
  lost: 'Lost during stocktake',
  culled: 'Culled',
  donated: 'Donated',
  harvest_finished: 'Harvest finished',
  sold: 'Sold',
  returned_available: 'Returned available',
  returned_quarantined: 'Returned quarantined',
  returned_discarded: 'Returned discarded',
  released_available: 'Released from quarantine',
  held_back: 'Held back from sale',
  retention_ended: 'Retention ended',
  corrected: 'Corrected'
}

// Every action the server offers on a plant. Which of them apply depends on
// the state, so nothing here is offered unconditionally; availableActions
// below mirrors the server's ALLOWED_FROM.
const OUTCOME_ACTIONS: Array<{ action: PlantOutcomeAction; label: string; variant: string }> = [
  { action: 'ready', label: 'Ready', variant: 'outline-success' },
  { action: 'hold-back', label: 'Hold back', variant: 'outline-warning' },
  { action: 'retain', label: 'Retain', variant: 'outline-primary' },
  { action: 'end-retention', label: 'End retention', variant: 'outline-primary' },
  { action: 'finish-harvest', label: 'Harvested', variant: 'outline-primary' },
  { action: 'fail', label: 'Failed', variant: 'outline-danger' },
  { action: 'cull', label: 'Cull', variant: 'outline-danger' },
  { action: 'donate', label: 'Donate', variant: 'outline-info' }
]

// The backward facts. Each says the situation changed rather than that
// something was recorded wrongly, and the server refuses one without a reason.
const REASON_REQUIRED_ACTIONS: Array<PlantOutcomeAction> = ['hold-back', 'end-retention']

const REASON_PROMPTS: Record<string, string> = {
  'hold-back': 'Why is this plant being held back from sale?',
  'end-retention': 'Why is this plant leaving retention?'
}

// The states each action may be recorded from, mirroring ALLOWED_FROM in
// plantings/lifecycle.py. A plant offered an action the server refuses gets a
// 400 with nothing on screen explaining it, so these are kept in step.
const ACTION_STATES: Record<PlantOutcomeAction, Array<PlantLifecycleState>> = {
  ready: ['growing'],
  'hold-back': ['available'],
  retain: ['growing', 'available', 'quarantined'],
  'end-retention': ['retained'],
  'finish-harvest': ['growing', 'available', 'retained'],
  fail: ['growing', 'available', 'retained', 'quarantined'],
  cull: ['growing', 'available', 'retained', 'quarantined'],
  donate: ['growing', 'available', 'retained']
}

function LifecycleStateBadge({ state }: { state: PlantLifecycleState }) {
  return <Badge bg={STATE_VARIANTS[state]}>{STATE_LABELS[state]}</Badge>
}

function PlantLifecycleBadge({ plant }: { plant: SpecificPlant }) {
  return <LifecycleStateBadge state={plant.lifecycle_state} />
}

function availableActions(plant: SpecificPlant): Array<{ action: PlantOutcomeAction; label: string; variant: string }> {
  return OUTCOME_ACTIONS.filter((entry) => ACTION_STATES[entry.action].includes(plant.lifecycle_state))
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

// A plant graded ready, held back, and graded ready again has more than one
// span. Only the latest state would hide every offer but the current one,
// which is exactly what the backward facts exist to keep.
function PlantAvailabilitySpans({ intervals }: { intervals: Array<AvailabilityInterval> }) {
  if (intervals.length === 0) {
    return <p className="text-muted mb-0">Never offered.</p>
  }
  return (
    <Table size="sm" className="mb-0">
      <thead>
        <tr>
          <th>Offered from</th>
          <th>Until</th>
        </tr>
      </thead>
      <tbody>
        {intervals.map((interval) => (
          <tr key={interval.started}>
            <td>{formatDateTime(interval.started)}</td>
            <td>{interval.ended === null ? 'Still offered' : formatDateTime(interval.ended)}</td>
          </tr>
        ))}
      </tbody>
    </Table>
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
                  <Button
                    size="sm"
                    variant="outline-secondary"
                    title="Record that this fact was never true. If it was true and the situation has since changed, use the outcome buttons instead."
                    onClick={() => onReverse(event)}
                  >
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

export {
  EVENT_LABELS,
  REASON_PROMPTS,
  REASON_REQUIRED_ACTIONS,
  STATE_LABELS,
  LifecycleStateBadge,
  PlantAvailabilitySpans,
  PlantLifecycleBadge,
  PlantLifecycleHistory,
  PlantOutcomeButtons
}
