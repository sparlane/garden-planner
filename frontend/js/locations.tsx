import React from 'react'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Card, Col, Form, Row, Table } from 'react-bootstrap'

import { createLocation, getLocationOccupancy, getLocations, updateLocation } from './api/locations'
import { queryKeys } from './query'
import { CapacityBasis, Location, LocationCreate, LocationType } from './types/locations'
import { ApiError } from './utils'

const TYPE_LABELS: Record<LocationType, string> = {
  site: 'Site',
  greenhouse: 'Greenhouse',
  tunnel: 'Tunnel',
  bench: 'Bench',
  bay: 'Bay',
  receiving: 'Receiving',
  storage: 'Storage',
  growing: 'Growing area',
  dispatch: 'Dispatch',
  hold: 'Customer hold',
  staging: 'Dispatch staging',
  quarantine: 'Quarantine',
  adjustment: 'Adjustment',
  seed_packet: 'Seed packet'
}

// Seed packet containers are created and retired with the packet itself, one
// per packet, so they would bury the operator's own places in the catalog.
const HIDDEN_TYPES: Array<LocationType> = ['seed_packet']

// The types an operator can choose. Adjustment is a ledger balancing point the
// stock workflows create against, not somewhere anything physically stands.
const SELECTABLE_TYPES: Array<LocationType> = ['site', 'greenhouse', 'tunnel', 'bench', 'bay', 'receiving', 'storage', 'growing', 'dispatch', 'hold', 'staging', 'quarantine']

const BASIS_LABELS: Record<CapacityBasis, string> = {
  none: 'Not tracked',
  trays: 'Trays',
  containers: 'Containers',
  plants: 'Plants',
  area: 'Area'
}

interface FormState {
  name: string
  code: string
  location_type: LocationType
  parent: string
  display_order: string
  capacity_basis: CapacityBasis
  capacity_value: string
  notes: string
}

const INITIAL_FORM: FormState = {
  name: '',
  code: '',
  location_type: 'bench',
  parent: '',
  display_order: '0',
  capacity_basis: 'none',
  capacity_value: '',
  notes: ''
}

interface LocationFormProps {
  parents: Array<Location>
  onCreated: () => void
}

function LocationForm({ parents, onCreated }: LocationFormProps) {
  const queryClient = useQueryClient()
  const [form, setForm] = React.useState<FormState>(INITIAL_FORM)
  const [fieldErrors, setFieldErrors] = React.useState<Record<string, string>>({})
  const createMutation = useMutation({
    mutationFn: (location: LocationCreate) => createLocation(location),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.locations.all })
      setForm(INITIAL_FORM)
      setFieldErrors({})
      onCreated()
    },
    onError: (error: unknown) => {
      setFieldErrors(errorsByField(error))
    }
  })

  function update<Field extends keyof FormState>(field: Field, value: FormState[Field]) {
    setForm((current) => ({ ...current, [field]: value }))
  }

  function submit(event: React.FormEvent) {
    event.preventDefault()
    const payload: LocationCreate = {
      name: form.name,
      code: form.code,
      location_type: form.location_type,
      display_order: Number(form.display_order) || 0,
      capacity_basis: form.capacity_basis,
      notes: form.notes
    }
    if (form.parent) payload.parent = Number(form.parent)
    // The server requires a value exactly when a basis is being measured, so
    // sending an empty string for an untracked place would be rejected.
    if (form.capacity_basis !== 'none') payload.capacity_value = form.capacity_value
    createMutation.mutate(payload)
  }

  return (
    <Card className="mb-3">
      <Card.Header>Add a location</Card.Header>
      <Card.Body>
        <Form onSubmit={submit}>
          <Row className="g-2">
            <Col md={3}>
              <Form.Group controlId="location-name">
                <Form.Label>Name</Form.Label>
                <Form.Control required value={form.name} isInvalid={'name' in fieldErrors} onChange={(event) => update('name', event.target.value)} />
                <Form.Control.Feedback type="invalid">{fieldErrors.name}</Form.Control.Feedback>
              </Form.Group>
            </Col>
            <Col md={2}>
              <Form.Group controlId="location-code">
                <Form.Label>Code</Form.Label>
                <Form.Control required value={form.code} isInvalid={'code' in fieldErrors} onChange={(event) => update('code', event.target.value)} />
                <Form.Control.Feedback type="invalid">{fieldErrors.code}</Form.Control.Feedback>
              </Form.Group>
            </Col>
            <Col md={2}>
              <Form.Group controlId="location-type">
                <Form.Label>Type</Form.Label>
                <Form.Select value={form.location_type} onChange={(event) => update('location_type', event.target.value as LocationType)}>
                  {SELECTABLE_TYPES.map((type) => (
                    <option key={type} value={type}>
                      {TYPE_LABELS[type]}
                    </option>
                  ))}
                </Form.Select>
              </Form.Group>
            </Col>
            <Col md={3}>
              <Form.Group controlId="location-parent">
                <Form.Label>Inside</Form.Label>
                <Form.Select value={form.parent} isInvalid={'parent' in fieldErrors} onChange={(event) => update('parent', event.target.value)}>
                  <option value="">Nothing — a top-level place</option>
                  {parents.map((parent) => (
                    <option key={parent.pk} value={parent.pk}>
                      {parent.full_name}
                    </option>
                  ))}
                </Form.Select>
                <Form.Control.Feedback type="invalid">{fieldErrors.parent}</Form.Control.Feedback>
              </Form.Group>
            </Col>
            <Col md={2}>
              <Form.Group controlId="location-order">
                <Form.Label>Order</Form.Label>
                <Form.Control type="number" value={form.display_order} onChange={(event) => update('display_order', event.target.value)} />
              </Form.Group>
            </Col>
            <Col md={3}>
              <Form.Group controlId="location-basis">
                <Form.Label>Capacity measured in</Form.Label>
                <Form.Select value={form.capacity_basis} onChange={(event) => update('capacity_basis', event.target.value as CapacityBasis)}>
                  {Object.entries(BASIS_LABELS).map(([basis, label]) => (
                    <option key={basis} value={basis}>
                      {label}
                    </option>
                  ))}
                </Form.Select>
              </Form.Group>
            </Col>
            {form.capacity_basis !== 'none' && (
              <Col md={2}>
                <Form.Group controlId="location-capacity">
                  <Form.Label>How many</Form.Label>
                  <Form.Control
                    required
                    value={form.capacity_value}
                    isInvalid={'capacity_value' in fieldErrors}
                    onChange={(event) => update('capacity_value', event.target.value)}
                  />
                  <Form.Control.Feedback type="invalid">{fieldErrors.capacity_value}</Form.Control.Feedback>
                </Form.Group>
              </Col>
            )}
            <Col md={7}>
              <Form.Group controlId="location-notes">
                <Form.Label>Notes</Form.Label>
                <Form.Control value={form.notes} onChange={(event) => update('notes', event.target.value)} />
              </Form.Group>
            </Col>
          </Row>
          {form.capacity_basis === 'area' && (
            <Alert variant="info" className="mt-2 mb-0 py-2">
              Area is recorded for planning. Nothing records a plant&apos;s footprint yet, so it does not stop anything being placed here.
            </Alert>
          )}
          <Button className="mt-3" type="submit" disabled={createMutation.isPending}>
            Add location
          </Button>
        </Form>
      </Card.Body>
    </Card>
  )
}

// DRF reports field errors as {field: [message]}; flatten to the first message
// per field so a form control can show it beneath itself.
function errorsByField(error: unknown): Record<string, string> {
  const body = error instanceof ApiError ? error.body : null
  if (!body || typeof body !== 'object') return {}
  const errors: Record<string, string> = {}
  for (const [field, detail] of Object.entries(body as Record<string, unknown>)) {
    errors[field] = Array.isArray(detail) ? String(detail[0]) : String(detail)
  }
  return errors
}

// Capacities arrive at their stored scale, so a bench holding two trays says
// "2.000". Only strip after a decimal point exists, or 100 would become 1.
function trimZeros(value: string | null): string {
  if (value === null) return ''
  return value.includes('.') ? value.replace(/0+$/, '').replace(/\.$/, '') : value
}

// A capacity can be exceeded when someone records a reason, so the remaining
// space is genuinely negative sometimes and must not be read as "still free".
function remainingSentence(remaining: string | null): string {
  if (remaining === null) return '. Area is recorded for planning and limits nothing yet.'
  const trimmed = trimZeros(remaining)
  if (trimmed.startsWith('-')) {
    return `, and is ${trimmed.slice(1)} over.`
  }
  return `, with ${trimmed} still free.`
}

interface OccupancyPanelProps {
  location: Location
}

function OccupancyPanel({ location }: OccupancyPanelProps) {
  const { data: occupancy } = useQuery({
    queryKey: queryKeys.locations.occupancy(location.pk),
    queryFn: ({ signal }) => getLocationOccupancy(location.pk, signal)
  })

  if (!occupancy) return null
  const tracked = occupancy.capacity_basis !== 'none'
  return (
    <Card>
      <Card.Header>What is standing in {location.full_name}</Card.Header>
      <Card.Body>
        <Table size="sm" className="mb-0">
          <thead>
            <tr>
              <th>Counted in</th>
              <th>Directly here</th>
              <th>Here and below</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>Trays</td>
              <td>{occupancy.here.trays}</td>
              <td>{occupancy.subtree.trays}</td>
            </tr>
            <tr>
              <td>Plants</td>
              <td>{occupancy.here.plants}</td>
              <td>{occupancy.subtree.plants}</td>
            </tr>
            <tr>
              <td>Containers</td>
              <td>{occupancy.here.containers}</td>
              <td>{occupancy.subtree.containers}</td>
            </tr>
          </tbody>
        </Table>
        {tracked && (
          <p className="mt-3 mb-0">
            Holds {trimZeros(occupancy.capacity_value)} {BASIS_LABELS[occupancy.capacity_basis].toLowerCase()}
            {remainingSentence(occupancy.remaining)}
          </p>
        )}
      </Card.Body>
    </Card>
  )
}

// The server orders locations by display order and name, which is the right
// order among siblings but puts a bay before the bench that contains it. The
// tree is rebuilt here so every child follows its own parent, which is the
// only order in which the indentation means anything.
function inTreeOrder(locations: Array<Location>): Array<Location> {
  const children = new Map<number | null, Array<Location>>()
  for (const location of locations) {
    const siblings = children.get(location.parent) ?? []
    siblings.push(location)
    children.set(location.parent, siblings)
  }
  // A child whose parent is filtered out of this view would otherwise vanish,
  // so anything unreachable from a root is treated as a root itself.
  const present = new Set(locations.map((location) => location.pk))
  const roots = locations.filter((location) => location.parent === null || !present.has(location.parent))
  const ordered: Array<Location> = []
  const visit = (location: Location) => {
    ordered.push(location)
    for (const child of children.get(location.pk) ?? []) {
      visit(child)
    }
  }
  roots.forEach(visit)
  return ordered
}

function LocationsCatalog() {
  const queryClient = useQueryClient()
  const [status, setStatus] = React.useState<'all' | 'active' | 'inactive'>('active')
  const [selectedPk, setSelectedPk] = React.useState<number | null>(null)
  const [retireError, setRetireError] = React.useState<string | null>(null)
  const active = status === 'all' ? undefined : status === 'active'
  const { data: locations = [] } = useQuery({
    queryKey: queryKeys.locations.list(status),
    queryFn: ({ signal }) => getLocations(signal, active)
  })
  // Parents are chosen from the whole catalog rather than the filtered view, so
  // hiding retired places does not silently shrink the choices.
  const { data: allLocations = [] } = useQuery({
    queryKey: queryKeys.locations.list('active'),
    queryFn: ({ signal }) => getLocations(signal, true)
  })
  const retireMutation = useMutation({
    mutationFn: (pk: number) => updateLocation(pk, { active: false }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.locations.all })
      setRetireError(null)
    },
    onError: (error: unknown) => {
      setRetireError(errorsByField(error).active ?? 'This location could not be retired.')
    }
  })

  const shown = inTreeOrder(locations.filter((location) => !HIDDEN_TYPES.includes(location.location_type)))
  const parents = allLocations.filter((location) => !HIDDEN_TYPES.includes(location.location_type))
  const selected = shown.find((location) => location.pk === selectedPk) ?? null

  return (
    <main className="container py-3">
      <h1 className="h3">Locations</h1>
      <p className="text-muted">
        Every place the workspace uses — sites, greenhouses, benches, stores, and dispatch areas. Stock, seed trays, and plants all stand in these same places.
      </p>
      <LocationForm parents={parents} onCreated={() => setStatus('active')} />
      {retireError && (
        <Alert variant="danger" onClose={() => setRetireError(null)} dismissible>
          {retireError}
        </Alert>
      )}
      <Card className="mb-3">
        <Card.Header>
          <Form.Group controlId="location-status" className="d-flex align-items-center gap-2 mb-0">
            <Form.Label className="mb-0">Showing</Form.Label>
            <Form.Select className="w-auto" value={status} onChange={(event) => setStatus(event.target.value as 'all' | 'active' | 'inactive')}>
              <option value="active">Active</option>
              <option value="inactive">Retired</option>
              <option value="all">All</option>
            </Form.Select>
          </Form.Group>
        </Card.Header>
        <Card.Body>
          <Table hover responsive size="sm">
            <thead>
              <tr>
                <th>Name</th>
                <th>Code</th>
                <th>Type</th>
                <th>Capacity</th>
                <th>Status</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {shown.map((location) => (
                <tr key={location.pk}>
                  <td style={{ paddingLeft: `${location.depth * 1.5 + 0.5}rem` }}>{location.name}</td>
                  <td>{location.code}</td>
                  <td>{TYPE_LABELS[location.location_type]}</td>
                  <td>{location.capacity_basis === 'none' ? 'Not tracked' : `${location.capacity_value} ${BASIS_LABELS[location.capacity_basis].toLowerCase()}`}</td>
                  <td>{location.active ? 'Active' : 'Retired'}</td>
                  <td className="text-nowrap">
                    <Button size="sm" variant="outline-primary" onClick={() => setSelectedPk(location.pk)}>
                      Occupancy
                    </Button>{' '}
                    {location.active && (
                      <Button size="sm" variant="outline-danger" disabled={retireMutation.isPending} onClick={() => retireMutation.mutate(location.pk)}>
                        Retire
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
              {shown.length === 0 && (
                <tr>
                  <td colSpan={6}>No locations match this filter.</td>
                </tr>
              )}
            </tbody>
          </Table>
        </Card.Body>
      </Card>
      {selected && <OccupancyPanel location={selected} />}
    </main>
  )
}

export { BASIS_LABELS, LocationsCatalog, TYPE_LABELS }
