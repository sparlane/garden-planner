import React from 'react'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Card, Col, Form, ListGroup, Row } from 'react-bootstrap'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router'

import {
  confirmGardenGeometry,
  createGardenArea,
  createGardenBed,
  createGardenRows,
  createGardenSquares,
  getGardenAreas,
  getGardenBeds,
  getGardenRows,
  getGardenSquares
} from '../api/garden'
import { createLocation, installHouseholdLocations } from '../api/locations'
import { updateWorkspace } from '../api/workspace'
import { GardenArea, GardenLengthUnit } from '../types/garden'
import { Location, LocationType } from '../types/locations'
import { MeasurementSystem, Workspace } from '../types/workspace'
import { LAYOUT_TEMPLATES, LayoutPlan, LayoutTemplate, expandTemplate, templateInfo } from './templates'
import { GardenCanvas } from './canvas'
import { errorsByField } from '../utils'
import { queryKeys } from '../query'

// The wizard never asks a gardener what a grid step should be. One step is one
// centimetre in a metric workspace and one inch in an imperial one, which is
// fine enough for a bed and coarse enough that a whole garden stays a sensible
// number of steps. Everything the gardener types is in metres or feet and is
// converted here.
interface GridUnits {
  step: GardenLengthUnit
  entry: string
  abbreviation: string
  stepsPerEntryUnit: number
}

const GRID_UNITS: Record<MeasurementSystem, GridUnits> = {
  metric: { step: 'cm', entry: 'metres', abbreviation: 'm', stepsPerEntryUnit: 100 },
  imperial: { step: 'in', entry: 'feet', abbreviation: 'ft', stepsPerEntryUnit: 12 }
}

type Step = 'basics' | 'area' | 'layout' | 'places' | 'done'

const STEP_VALUES: Array<Step> = ['basics', 'area', 'layout', 'places', 'done']

function parseStep(value: string | null, fallback: Step): Step {
  return STEP_VALUES.find((step) => step === value) ?? fallback
}

const STEPS: Array<{ value: Step; label: string }> = [
  { value: 'basics', label: 'Your garden' },
  { value: 'area', label: 'The space' },
  { value: 'layout', label: 'Beds and rows' },
  { value: 'places', label: 'Where things are kept' },
  { value: 'done', label: 'Done' }
]

function toSteps(value: string, units: GridUnits): number {
  const entered = Number(value)
  if (!Number.isFinite(entered) || entered <= 0) {
    return 0
  }
  return Math.max(1, Math.round(entered * units.stepsPerEntryUnit))
}

function fromSteps(value: number, units: GridUnits): string {
  return String(Math.round((value / units.stepsPerEntryUnit) * 100) / 100)
}

// A location code has to be unique in the workspace and is shown to the
// gardener, so it is derived from what they typed rather than generated. A
// collision comes back as a field error on `code`, which is honest: they
// already have a place called that.
function codeFromName(name: string): string {
  return name
    .toUpperCase()
    .replace(/[^A-Z0-9]+/g, '-')
    .replace(/^-|-$/g, '')
    .slice(0, 64)
}

function StepList({ current }: { current: Step }) {
  const currentIndex = STEPS.findIndex((step) => step.value === current)
  return (
    <ListGroup horizontal="md" className="mb-4">
      {STEPS.map((step, index) => (
        <ListGroup.Item key={step.value} active={index === currentIndex} disabled={index > currentIndex}>
          {index + 1}. {step.label}
        </ListGroup.Item>
      ))}
    </ListGroup>
  )
}

function BasicsStep({ workspace, onDone }: { workspace: Workspace; onDone: () => void }) {
  const queryClient = useQueryClient()
  const [name, setName] = React.useState(workspace.name)
  const [timezone, setTimezone] = React.useState(workspace.timezone)
  const [measurementSystem, setMeasurementSystem] = React.useState<MeasurementSystem>(workspace.measurement_system)
  const [fieldErrors, setFieldErrors] = React.useState<Record<string, string>>({})

  const mutation = useMutation({
    mutationFn: () => updateWorkspace({ name, timezone, measurement_system: measurementSystem }),
    onSuccess: (updated) => {
      // Written straight into the cache, as the settings screen does, so the
      // navigation bar carries the new name from the next step onwards.
      queryClient.setQueryData(queryKeys.workspace.current, updated)
      setFieldErrors({})
      onDone()
    },
    onError: (error) => setFieldErrors(errorsByField(error))
  })

  return (
    <Card>
      <Card.Body>
        <Card.Title>What should this garden be called?</Card.Title>
        <Card.Text className="text-muted">These are recorded before anything is dated or measured, because everything after this is written in them.</Card.Text>
        <Form
          onSubmit={(event) => {
            event.preventDefault()
            mutation.mutate()
          }}
        >
          <Form.Group className="mb-3" controlId="setup-name">
            <Form.Label>Garden name</Form.Label>
            <Form.Control value={name} onChange={(event) => setName(event.target.value)} isInvalid={'name' in fieldErrors} required maxLength={255} />
            <Form.Control.Feedback type="invalid">{fieldErrors.name}</Form.Control.Feedback>
          </Form.Group>
          <Form.Group className="mb-3" controlId="setup-timezone">
            <Form.Label>Time zone</Form.Label>
            <Form.Control value={timezone} onChange={(event) => setTimezone(event.target.value)} isInvalid={'timezone' in fieldErrors} required maxLength={64} />
            <Form.Text>An IANA name, such as UTC or Pacific/Auckland. Dates you record are read in it.</Form.Text>
            <Form.Control.Feedback type="invalid">{fieldErrors.timezone}</Form.Control.Feedback>
          </Form.Group>
          <Form.Group className="mb-3" controlId="setup-measurement">
            <Form.Label>Measurements</Form.Label>
            <Form.Select value={measurementSystem} onChange={(event) => setMeasurementSystem(event.target.value as MeasurementSystem)}>
              <option value="metric">Metric — metres and centimetres</option>
              <option value="imperial">Imperial — feet and inches</option>
            </Form.Select>
          </Form.Group>
          <Button type="submit" disabled={mutation.isPending}>
            {mutation.isPending ? 'Saving…' : 'Continue'}
          </Button>
        </Form>
      </Card.Body>
    </Card>
  )
}

function AreaStep({ workspace, onCreated }: { workspace: Workspace; onCreated: (area: GardenArea) => void }) {
  const queryClient = useQueryClient()
  const units = GRID_UNITS[workspace.measurement_system]
  const [name, setName] = React.useState('Back garden')
  const [width, setWidth] = React.useState('10')
  const [length, setLength] = React.useState('8')
  const [fieldErrors, setFieldErrors] = React.useState<Record<string, string>>({})
  const sizeX = toSteps(width, units)
  const sizeY = toSteps(length, units)

  const mutation = useMutation({
    mutationFn: async () => {
      const area = await createGardenArea({ name, size_x: sizeX, size_y: sizeY })
      // Stated in the same breath as the dimensions, because the integers
      // above mean nothing until something says what one of them measures.
      await confirmGardenGeometry(area.pk, { length_unit: units.step, cell_length: '1', notes: 'Recorded during guided setup.' })
      return area
    },
    onSuccess: (area) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.garden.all })
      setFieldErrors({})
      onCreated(area)
    },
    onError: (error) => setFieldErrors(errorsByField(error))
  })

  return (
    <Card>
      <Card.Body>
        <Card.Title>How big is the space?</Card.Title>
        <Card.Text className="text-muted">Roughly is fine. This is the ground the beds are placed on, and it can be corrected later.</Card.Text>
        <Form
          onSubmit={(event) => {
            event.preventDefault()
            mutation.mutate()
          }}
        >
          <Form.Group className="mb-3" controlId="setup-area-name">
            <Form.Label>Name for this space</Form.Label>
            <Form.Control value={name} onChange={(event) => setName(event.target.value)} isInvalid={'name' in fieldErrors} required />
            <Form.Control.Feedback type="invalid">{fieldErrors.name}</Form.Control.Feedback>
          </Form.Group>
          <Row>
            <Col md={6}>
              <Form.Group className="mb-3" controlId="setup-area-width">
                <Form.Label>Width in {units.entry}</Form.Label>
                <Form.Control type="number" min="0.1" step="0.1" value={width} onChange={(event) => setWidth(event.target.value)} isInvalid={'size_x' in fieldErrors} required />
                <Form.Control.Feedback type="invalid">{fieldErrors.size_x}</Form.Control.Feedback>
              </Form.Group>
            </Col>
            <Col md={6}>
              <Form.Group className="mb-3" controlId="setup-area-length">
                <Form.Label>Length in {units.entry}</Form.Label>
                <Form.Control type="number" min="0.1" step="0.1" value={length} onChange={(event) => setLength(event.target.value)} isInvalid={'size_y' in fieldErrors} required />
                <Form.Control.Feedback type="invalid">{fieldErrors.size_y}</Form.Control.Feedback>
              </Form.Group>
            </Col>
          </Row>
          <p className="text-muted">
            That is {sizeX} × {sizeY} {units.step === 'cm' ? 'centimetres' : 'inches'}, which is what beds are measured against.
          </p>
          <Button type="submit" disabled={mutation.isPending || sizeX === 0 || sizeY === 0}>
            {mutation.isPending ? 'Creating…' : 'Create this space'}
          </Button>
        </Form>
      </Card.Body>
    </Card>
  )
}

function LayoutStep({ area, workspace, onDone }: { area: GardenArea; workspace: Workspace; onDone: () => void }) {
  const queryClient = useQueryClient()
  const units = GRID_UNITS[workspace.measurement_system]
  const [plan, setPlan] = React.useState<LayoutPlan>({
    template: 'raised_bed',
    name: 'Bed 1',
    placement_x: 0,
    placement_y: 0,
    size_x: Math.min(area.size_x, units.stepsPerEntryUnit * 2),
    size_y: Math.min(area.size_y, units.stepsPerEntryUnit),
    divisions: 4,
    columns: 4
  })
  const [fieldErrors, setFieldErrors] = React.useState<Record<string, string>>({})
  const [laid, setLaid] = React.useState(0)

  const { data: beds = [] } = useQuery({ queryKey: queryKeys.garden.beds, queryFn: ({ signal }) => getGardenBeds(signal) })
  const { data: rows = [] } = useQuery({ queryKey: queryKeys.garden.rows, queryFn: ({ signal }) => getGardenRows(signal) })
  const { data: squares = [] } = useQuery({ queryKey: queryKeys.garden.squares, queryFn: ({ signal }) => getGardenSquares(signal) })

  const info = templateInfo(plan.template)
  const expanded = expandTemplate(plan)
  const areaBeds = beds.filter((bed) => bed.area === area.pk)
  const bedPks = new Set(areaBeds.map((bed) => bed.pk))
  // The bed being planned is drawn with a negative key so it cannot collide
  // with a saved one, and its children hang off the same key.
  const previewBedPk = -1
  const previewBeds = [
    ...areaBeds,
    {
      pk: previewBedPk,
      area: area.pk,
      name: plan.name,
      kind: expanded.kind,
      placement_x: plan.placement_x,
      placement_y: plan.placement_y,
      size_x: plan.size_x,
      size_y: plan.size_y
    }
  ]
  const previewRows = [...rows.filter((row) => bedPks.has(row.bed)), ...expanded.rows.map((row, index) => ({ ...row, pk: -(index + 1), bed: previewBedPk }))]
  const previewSquares = [
    ...squares.filter((square) => bedPks.has(square.bed)),
    ...expanded.squares.map((square, index) => ({ ...square, pk: -(index + 1), bed: previewBedPk, area: area.pk }))
  ]

  const dividedButEmpty = info.divides !== 'none' && expanded.rows.length === 0 && expanded.squares.length === 0

  const mutation = useMutation({
    mutationFn: async () => {
      const bed = await createGardenBed({
        area: area.pk,
        name: plan.name,
        kind: expanded.kind,
        placement_x: plan.placement_x,
        placement_y: plan.placement_y,
        size_x: plan.size_x,
        size_y: plan.size_y
      })
      if (expanded.rows.length > 0) {
        await createGardenRows(expanded.rows.map((row) => ({ ...row, bed: bed.pk })))
      }
      if (expanded.squares.length > 0) {
        await createGardenSquares(expanded.squares.map((square) => ({ ...square, bed: bed.pk })))
      }
      return bed
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.garden.all })
      setFieldErrors({})
      setLaid((count) => count + 1)
      setPlan((current) => ({
        ...current,
        name: `Bed ${laid + 2}`,
        placement_y: Math.min(area.size_y, current.placement_y + current.size_y + Math.round(units.stepsPerEntryUnit / 2))
      }))
    },
    onError: (error) => setFieldErrors(errorsByField(error))
  })

  function updateSize(field: 'size_x' | 'size_y' | 'placement_x' | 'placement_y', value: string) {
    setPlan((current) => ({ ...current, [field]: field.startsWith('size') ? toSteps(value, units) : Math.max(0, Math.round(Number(value) * units.stepsPerEntryUnit)) }))
  }

  return (
    <Card>
      <Card.Body>
        <Card.Title>What is growing in it?</Card.Title>
        <Card.Text className="text-muted">Add a bed, see where it lands, then save it. You can add as many as you like, or skip this and come back.</Card.Text>
        {laid > 0 && (
          <Alert variant="success">
            {laid} {laid === 1 ? 'bed has' : 'beds have'} been laid out.
          </Alert>
        )}
        {'non_field_errors' in fieldErrors && <Alert variant="danger">{fieldErrors.non_field_errors}</Alert>}
        <Row>
          <Col lg={6}>
            <Form
              onSubmit={(event) => {
                event.preventDefault()
                mutation.mutate()
              }}
            >
              <Form.Group className="mb-3" controlId="setup-template">
                <Form.Label>Kind of bed</Form.Label>
                <Form.Select value={plan.template} onChange={(event) => setPlan((current) => ({ ...current, template: event.target.value as LayoutTemplate }))}>
                  {LAYOUT_TEMPLATES.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label} — {option.description}
                    </option>
                  ))}
                </Form.Select>
              </Form.Group>
              <Form.Group className="mb-3" controlId="setup-bed-name">
                <Form.Label>Name</Form.Label>
                <Form.Control value={plan.name} onChange={(event) => setPlan((current) => ({ ...current, name: event.target.value }))} isInvalid={'name' in fieldErrors} required />
                <Form.Control.Feedback type="invalid">{fieldErrors.name}</Form.Control.Feedback>
              </Form.Group>
              <Row>
                <Col sm={6}>
                  <Form.Group className="mb-3" controlId="setup-bed-width">
                    <Form.Label>Width in {units.entry}</Form.Label>
                    <Form.Control
                      type="number"
                      min="0.1"
                      step="0.1"
                      value={fromSteps(plan.size_x, units)}
                      onChange={(event) => updateSize('size_x', event.target.value)}
                      required
                    />
                  </Form.Group>
                </Col>
                <Col sm={6}>
                  <Form.Group className="mb-3" controlId="setup-bed-length">
                    <Form.Label>Length in {units.entry}</Form.Label>
                    <Form.Control
                      type="number"
                      min="0.1"
                      step="0.1"
                      value={fromSteps(plan.size_y, units)}
                      onChange={(event) => updateSize('size_y', event.target.value)}
                      required
                    />
                  </Form.Group>
                </Col>
              </Row>
              <Row>
                <Col sm={6}>
                  <Form.Group className="mb-3" controlId="setup-bed-x">
                    <Form.Label>From the left, in {units.entry}</Form.Label>
                    <Form.Control
                      type="number"
                      min="0"
                      step="0.1"
                      value={fromSteps(plan.placement_x, units)}
                      onChange={(event) => updateSize('placement_x', event.target.value)}
                      isInvalid={'placement_x' in fieldErrors}
                    />
                    <Form.Control.Feedback type="invalid">{fieldErrors.placement_x}</Form.Control.Feedback>
                  </Form.Group>
                </Col>
                <Col sm={6}>
                  <Form.Group className="mb-3" controlId="setup-bed-y">
                    <Form.Label>From the bottom, in {units.entry}</Form.Label>
                    <Form.Control
                      type="number"
                      min="0"
                      step="0.1"
                      value={fromSteps(plan.placement_y, units)}
                      onChange={(event) => updateSize('placement_y', event.target.value)}
                      isInvalid={'placement_y' in fieldErrors}
                    />
                    <Form.Control.Feedback type="invalid">{fieldErrors.placement_y}</Form.Control.Feedback>
                  </Form.Group>
                </Col>
              </Row>
              {info.divides !== 'none' && (
                <Row>
                  <Col sm={6}>
                    <Form.Group className="mb-3" controlId="setup-divisions">
                      <Form.Label>{info.divides === 'rows' ? 'How many rows' : 'Cells up the bed'}</Form.Label>
                      <Form.Control
                        type="number"
                        min="1"
                        step="1"
                        value={plan.divisions}
                        onChange={(event) => setPlan((current) => ({ ...current, divisions: Number(event.target.value) }))}
                      />
                    </Form.Group>
                  </Col>
                  {info.divides === 'grid' && (
                    <Col sm={6}>
                      <Form.Group className="mb-3" controlId="setup-columns">
                        <Form.Label>Cells across the bed</Form.Label>
                        <Form.Control
                          type="number"
                          min="1"
                          step="1"
                          value={plan.columns}
                          onChange={(event) => setPlan((current) => ({ ...current, columns: Number(event.target.value) }))}
                        />
                      </Form.Group>
                    </Col>
                  )}
                </Row>
              )}
              {dividedButEmpty && <Alert variant="warning">This bed is too small to divide that many ways. Make it longer, or ask for fewer.</Alert>}
              <Button type="submit" disabled={mutation.isPending || dividedButEmpty}>
                {mutation.isPending ? 'Saving…' : 'Add this bed'}
              </Button>{' '}
              <Button variant="secondary" onClick={onDone} disabled={mutation.isPending}>
                {laid > 0 ? 'Continue' : 'Skip beds for now'}
              </Button>
            </Form>
          </Col>
          <Col lg={6}>
            <h3 className="h6">Before you save</h3>
            <p className="text-muted">
              The bed you are describing is drawn with what is already here. {expanded.rows.length > 0 && `${expanded.rows.length} rows. `}
              {expanded.squares.length > 0 && `${expanded.squares.length} cells.`}
            </p>
            <div className="garden-setup-preview">
              <GardenCanvas area={area} beds={previewBeds} rows={previewRows} squares={previewSquares} />
            </div>
          </Col>
        </Row>
      </Card.Body>
    </Card>
  )
}

function ExtraPlaceForm({ label, type, onCreated }: { label: string; type: LocationType; onCreated: (location: Location) => void }) {
  const queryClient = useQueryClient()
  const [name, setName] = React.useState('')
  const [fieldErrors, setFieldErrors] = React.useState<Record<string, string>>({})

  const mutation = useMutation({
    mutationFn: async () => {
      const response = await createLocation({ name, code: codeFromName(name), location_type: type })
      return (await response.json()) as Location
    },
    onSuccess: (location) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.locations.all })
      setFieldErrors({})
      setName('')
      onCreated(location)
    },
    onError: (error) => setFieldErrors(errorsByField(error))
  })

  return (
    <Form
      className="mb-3"
      onSubmit={(event) => {
        event.preventDefault()
        mutation.mutate()
      }}
    >
      <Form.Group controlId={`setup-extra-${type}`}>
        <Form.Label>{label}</Form.Label>
        <Row>
          <Col sm={8}>
            <Form.Control
              value={name}
              onChange={(event) => setName(event.target.value)}
              isInvalid={'name' in fieldErrors || 'code' in fieldErrors}
              placeholder="Leave blank if you have none"
            />
            <Form.Control.Feedback type="invalid">{fieldErrors.name ?? fieldErrors.code}</Form.Control.Feedback>
          </Col>
          <Col sm={4}>
            <Button type="submit" variant="outline-secondary" disabled={mutation.isPending || name.trim() === ''}>
              Add
            </Button>
          </Col>
        </Row>
      </Form.Group>
    </Form>
  )
}

function PlacesStep({ onDone }: { onDone: () => void }) {
  const queryClient = useQueryClient()
  const [installed, setInstalled] = React.useState<Array<Location>>([])
  const [extras, setExtras] = React.useState<Array<Location>>([])

  const mutation = useMutation({
    mutationFn: installHouseholdLocations,
    onSuccess: (locations) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.locations.all })
      setInstalled(locations)
    }
  })

  return (
    <Card>
      <Card.Body>
        <Card.Title>Where do you keep things?</Card.Title>
        <Card.Text className="text-muted">
          Seed packets, trays, and anything you feed the garden with have to be somewhere. These are the ordinary places a garden has; adding them now means nothing is blocked
          later. Asking twice creates nothing new.
        </Card.Text>
        <Button onClick={() => mutation.mutate()} disabled={mutation.isPending}>
          {mutation.isPending ? 'Adding…' : 'Add the usual places'}
        </Button>
        {installed.length > 0 && (
          <ListGroup className="mt-3">
            {installed.map((location) => (
              <ListGroup.Item key={location.pk}>{location.full_name}</ListGroup.Item>
            ))}
          </ListGroup>
        )}
        <hr />
        <h3 className="h6">Anything else?</h3>
        <ExtraPlaceForm label="A greenhouse or tunnel" type="greenhouse" onCreated={(location) => setExtras((current) => [...current, location])} />
        <ExtraPlaceForm label="Pots or containers" type="container" onCreated={(location) => setExtras((current) => [...current, location])} />
        {extras.length > 0 && (
          <ListGroup className="mb-3">
            {extras.map((location) => (
              <ListGroup.Item key={location.pk}>{location.full_name}</ListGroup.Item>
            ))}
          </ListGroup>
        )}
        <Button variant="secondary" onClick={onDone}>
          Continue
        </Button>
      </Card.Body>
    </Card>
  )
}

function DoneStep({ area }: { area?: GardenArea }) {
  return (
    <Card>
      <Card.Body>
        <Card.Title>Your garden is ready</Card.Title>
        <Card.Text className="text-muted">Nothing here is final. Everything you have just described can be added to or corrected.</Card.Text>
        <ListGroup>
          <ListGroup.Item action as={Link} to="/plantings/garden-squares">
            Sow some seed
          </ListGroup.Item>
          <ListGroup.Item action as={Link} to="/plants">
            Add a plant you already have
          </ListGroup.Item>
          <ListGroup.Item action as={Link} to={area === undefined ? '/gardens' : `/gardens/${area.pk}`}>
            Look at the garden
          </ListGroup.Item>
        </ListGroup>
      </Card.Body>
    </Card>
  )
}

// The wizard commits real records as it goes and keeps the area it is working
// on in the URL, so leaving at any point and coming back resumes where the
// records say it got to rather than replaying anything.
function GardenSetup({ workspace }: { workspace: Workspace }) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { areaId } = useParams()
  const [searchParams, setSearchParams] = useSearchParams()
  const areaPk = areaId === undefined ? undefined : Number(areaId)
  // The step lives in the address beside the area, so reloading on "where do
  // you keep things" comes back to it rather than to the beds behind it. The
  // area alone would only ever resume at the layout step.
  const step = parseStep(searchParams.get('step'), areaPk === undefined ? 'basics' : 'layout')

  function setStep(next: Step) {
    setSearchParams({ step: next }, { replace: true })
  }

  const { data: areas = [], isPending } = useQuery({ queryKey: queryKeys.garden.areas, queryFn: ({ signal }) => getGardenAreas(signal) })
  const area = areaPk === undefined ? undefined : areas.find((candidate) => candidate.pk === areaPk)

  const stateMutation = useMutation({
    mutationFn: (state: Workspace['garden_setup_state']) => updateWorkspace({ garden_setup_state: state }),
    onSuccess: (updated) => queryClient.setQueryData(queryKeys.workspace.current, updated)
  })

  function leave(state: Workspace['garden_setup_state'], to: string) {
    stateMutation.mutate(state, { onSettled: () => navigate(to) })
  }

  if (areaPk !== undefined && area === undefined && !isPending) {
    return (
      <Alert variant="warning">
        That garden area no longer exists. <Link to="/setup">Start again</Link>.
      </Alert>
    )
  }

  return (
    <>
      <h1 className="h3">Set up your garden</h1>
      <StepList current={step} />
      {step === 'basics' && <BasicsStep workspace={workspace} onDone={() => setStep('area')} />}
      {step === 'area' && (
        <AreaStep
          workspace={workspace}
          onCreated={(created) => {
            // Replaced rather than pushed, so Back does not return to a form
            // that would create a second area for the same answers.
            navigate(`/setup/${created.pk}?step=layout`, { replace: true })
          }}
        />
      )}
      {step === 'layout' &&
        (area === undefined ? <p>Loading the space you were working on…</p> : <LayoutStep area={area} workspace={workspace} onDone={() => setStep('places')} />)}
      {step === 'places' && <PlacesStep onDone={() => setStep('done')} />}
      {step === 'done' && <DoneStep area={area} />}
      <p className="mt-4">
        {step === 'done' ? (
          <Button variant="primary" onClick={() => leave('complete', area === undefined ? '/gardens' : `/gardens/${area.pk}`)}>
            Finish
          </Button>
        ) : (
          <Button variant="link" className="px-0" onClick={() => leave('skipped', '/gardens')}>
            Skip setup for now
          </Button>
        )}
      </p>
    </>
  )
}

export { GRID_UNITS, GardenSetup, codeFromName, toSteps }
