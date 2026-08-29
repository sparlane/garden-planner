import React, { useMemo, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Alert, Badge, Button, ButtonGroup, Card, Col, Form, Row, Table } from 'react-bootstrap'
import { NavLink, useParams } from 'react-router'

import { correctGardenRegisterStatus, finishGardenRegisterCrop, getGardenRegister, getGardenRegisterDetail } from '../api/plantings'
import { getGardenSquares } from '../api/garden'
import { getLocations } from '../api/locations'
import { getPlants, getPlantVarieties } from '../api/plants'
import { queryClient, queryKeys } from '../query'
import { GardenPlantingSource, GardenRegisterFilters } from '../types/plantings'
import { formatDate, formatDateRange } from '../utils'
import { GardenQuickAddButton } from './garden_quick_add'

const SOURCES: Array<{ value: GardenPlantingSource; label: string }> = [
  { value: 'direct_seed', label: 'Direct seed' },
  { value: 'indoor_raised_seed', label: 'Indoor-raised seed' },
  { value: 'purchased_plant', label: 'Purchased plant' },
  { value: 'cutting', label: 'Cutting' },
  { value: 'division', label: 'Division' },
  { value: 'bulb_tuber_corm', label: 'Bulb, tuber or corm' },
  { value: 'bare_root', label: 'Bare root' },
  { value: 'volunteer', label: 'Volunteer' },
  { value: 'existing_unknown', label: 'Existing or unknown' }
]

function sourceLabel(source: GardenPlantingSource): string {
  return SOURCES.find((entry) => entry.value === source)?.label ?? source
}

function GardenRegisterView() {
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [state, setState] = useState<'current' | 'finished' | 'all'>('current')
  const [source, setSource] = useState<GardenPlantingSource | ''>('')
  const [health, setHealth] = useState('')
  const [nextTask, setNextTask] = useState('')
  const [plantedFrom, setPlantedFrom] = useState('')
  const [plantedTo, setPlantedTo] = useState('')
  const [crop, setCrop] = useState('')
  const [variety, setVariety] = useState('')
  const [location, setLocation] = useState('')
  const [harvestFrom, setHarvestFrom] = useState('')
  const [harvestTo, setHarvestTo] = useState('')
  const { data: crops = [] } = useQuery({ queryKey: ['plants', 'garden-register'], queryFn: ({ signal }) => getPlants(signal) })
  const { data: varieties = [] } = useQuery({ queryKey: ['plant-varieties', 'garden-register'], queryFn: ({ signal }) => getPlantVarieties(signal) })
  const { data: squares = [] } = useQuery({ queryKey: ['garden-squares', 'garden-register'], queryFn: ({ signal }) => getGardenSquares(signal) })
  const { data: locations = [] } = useQuery({ queryKey: ['locations', 'garden-register'], queryFn: ({ signal }) => getLocations(signal, true) })
  const filters = useMemo<GardenRegisterFilters>(
    () => ({
      page,
      search: search || undefined,
      state,
      source: source || undefined,
      health: health === '' ? undefined : health === 'true',
      next_task: nextTask === '' ? undefined : nextTask === 'true',
      planted_from: plantedFrom || undefined,
      planted_to: plantedTo || undefined,
      crop: crop ? Number(crop) : undefined,
      variety: variety ? Number(variety) : undefined,
      location: location || undefined,
      expected_harvest_from: harvestFrom || undefined,
      expected_harvest_to: harvestTo || undefined
    }),
    [page, search, state, source, health, nextTask, plantedFrom, plantedTo, crop, variety, location, harvestFrom, harvestTo]
  )
  const { data, isPending } = useQuery({
    queryKey: queryKeys.plantings.gardenRegister(filters),
    queryFn: ({ signal }) => getGardenRegister(filters, signal)
  })
  const lastPage = Math.max(1, Math.ceil((data?.count ?? 0) / 40))
  function narrow<T>(setter: React.Dispatch<React.SetStateAction<T>>) {
    return (value: T) => {
      setPage(1)
      setter(value)
    }
  }

  return (
    <main className="container py-3">
      <div className="d-flex justify-content-between align-items-start gap-3 mb-3">
        <div>
          <h1>What’s growing</h1>
          <p className="text-muted mb-0">Crop patches and individually tracked plants, together without double counting.</p>
        </div>
        <GardenQuickAddButton />
      </div>
      <Row className="g-2 mb-3">
        <Col md={4}>
          <Form.Label htmlFor="garden-register-search">Search crop, variety, name or place</Form.Label>
          <Form.Control id="garden-register-search" value={search} onChange={(event) => narrow(setSearch)(event.target.value)} />
        </Col>
        <Col md={2}>
          <Form.Label htmlFor="garden-register-state">Show</Form.Label>
          <Form.Select id="garden-register-state" value={state} onChange={(event) => narrow(setState)(event.target.value as typeof state)}>
            <option value="current">Growing now</option>
            <option value="finished">Finished history</option>
            <option value="all">All records</option>
          </Form.Select>
        </Col>
        <Col md={2}>
          <Form.Label htmlFor="garden-register-crop">Crop</Form.Label>
          <Form.Select id="garden-register-crop" value={crop} onChange={(event) => narrow(setCrop)(event.target.value)}>
            <option value="">Any crop</option>
            {crops.map((entry) => (
              <option key={entry.pk} value={entry.pk}>
                {entry.name}
              </option>
            ))}
          </Form.Select>
        </Col>
        <Col md={2}>
          <Form.Label htmlFor="garden-register-variety">Variety</Form.Label>
          <Form.Select id="garden-register-variety" value={variety} onChange={(event) => narrow(setVariety)(event.target.value)}>
            <option value="">Any variety</option>
            {varieties
              .filter((entry) => !crop || entry.plant === Number(crop))
              .map((entry) => (
                <option key={entry.pk} value={entry.pk}>
                  {entry.name}
                </option>
              ))}
          </Form.Select>
        </Col>
        <Col md={3}>
          <Form.Label htmlFor="garden-register-location">Location</Form.Label>
          <Form.Select id="garden-register-location" value={location} onChange={(event) => narrow(setLocation)(event.target.value)}>
            <option value="">Anywhere</option>
            <option value="unplaced">Unplaced</option>
            <optgroup label="Garden squares">
              {squares.map((entry) => (
                <option key={`square-${entry.pk}`} value={`square:${entry.pk}`}>
                  {entry.name}
                </option>
              ))}
            </optgroup>
            <optgroup label="Places and containers">
              {locations.map((entry) => (
                <option key={`location-${entry.pk}`} value={`location:${entry.pk}`}>
                  {entry.name}
                </option>
              ))}
            </optgroup>
          </Form.Select>
        </Col>
        <Col md={2}>
          <Form.Label htmlFor="garden-register-source">Source</Form.Label>
          <Form.Select id="garden-register-source" value={source} onChange={(event) => narrow(setSource)(event.target.value as GardenPlantingSource | '')}>
            <option value="">Any source</option>
            {SOURCES.map((entry) => (
              <option key={entry.value} value={entry.value}>
                {entry.label}
              </option>
            ))}
          </Form.Select>
        </Col>
        <Col md={2}>
          <Form.Label htmlFor="garden-register-harvest-from">Harvest from</Form.Label>
          <Form.Control id="garden-register-harvest-from" type="date" value={harvestFrom} onChange={(event) => narrow(setHarvestFrom)(event.target.value)} />
        </Col>
        <Col md={2}>
          <Form.Label htmlFor="garden-register-harvest-to">Harvest to</Form.Label>
          <Form.Control id="garden-register-harvest-to" type="date" value={harvestTo} onChange={(event) => narrow(setHarvestTo)(event.target.value)} />
        </Col>
        <Col md={2}>
          <Form.Label htmlFor="garden-register-health">Health</Form.Label>
          <Form.Select id="garden-register-health" value={health} onChange={(event) => narrow(setHealth)(event.target.value)}>
            <option value="">Any</option>
            <option value="true">Flagged</option>
            <option value="false">No flag</option>
          </Form.Select>
        </Col>
        <Col md={2}>
          <Form.Label htmlFor="garden-register-task">Next task</Form.Label>
          <Form.Select id="garden-register-task" value={nextTask} onChange={(event) => narrow(setNextTask)(event.target.value)}>
            <option value="">Any</option>
            <option value="true">Has task</option>
            <option value="false">No task</option>
          </Form.Select>
        </Col>
        <Col md={2}>
          <Form.Label htmlFor="garden-register-from">Planted from</Form.Label>
          <Form.Control id="garden-register-from" type="date" value={plantedFrom} onChange={(event) => narrow(setPlantedFrom)(event.target.value)} />
        </Col>
        <Col md={2}>
          <Form.Label htmlFor="garden-register-to">Planted to</Form.Label>
          <Form.Control id="garden-register-to" type="date" value={plantedTo} onChange={(event) => narrow(setPlantedTo)(event.target.value)} />
        </Col>
      </Row>
      {data && (
        <div className="d-flex flex-wrap gap-2 mb-3" aria-label="Filtered garden totals">
          <Badge bg="primary">{data.totals.quantity} plants</Badge>
          <Badge bg="secondary">{data.totals.aggregate_rows} crop patches</Badge>
          <Badge bg="secondary">{data.totals.individual_plants} individuals</Badge>
          <Badge bg="success">{data.totals.perennials} perennials</Badge>
          {data.totals.containers > 0 && <Badge bg="info">{data.totals.containers} in containers</Badge>}
          {data.totals.unplaced > 0 && (
            <Badge bg="warning" text="dark">
              {data.totals.unplaced} unplaced
            </Badge>
          )}
          {data.totals.health_flags > 0 && <Badge bg="danger">{data.totals.health_flags} health flags</Badge>}
        </div>
      )}
      {isPending ? (
        <p>Loading garden plants…</p>
      ) : data?.results.length === 0 ? (
        <Alert variant="light">No plantings match these filters.</Alert>
      ) : (
        <Table responsive hover>
          <thead>
            <tr>
              <th>Crop</th>
              <th>Type / quantity</th>
              <th>Location</th>
              <th>Planted</th>
              <th>Expected harvest</th>
              <th>Next</th>
            </tr>
          </thead>
          <tbody>
            {data?.results.map((row) => (
              <tr key={row.key}>
                <td>
                  <NavLink to={`/plantings/garden-register/${row.key}`}>{row.name || row.plant_name}</NavLink>
                  <div className="small text-muted">
                    {row.variety_name} · {sourceLabel(row.source)}
                  </div>
                </td>
                <td>
                  {row.record_type === 'aggregate' ? 'Crop or patch' : 'Individual'} · {row.quantity_is_approximate ? 'about ' : ''}
                  {row.quantity}
                  {row.perennial ? (
                    <Badge bg="success" className="ms-1">
                      perennial
                    </Badge>
                  ) : null}
                </td>
                <td>
                  {row.location_label}
                  {row.container ? (
                    <Badge bg="info" className="ms-1">
                      container
                    </Badge>
                  ) : null}
                </td>
                <td>
                  {row.date_is_approximate ? 'About ' : ''}
                  {formatDate(row.planted_on)}
                </td>
                <td>{formatDateRange(row.expected_harvest_early, row.expected_harvest_late) || 'Not estimated'}</td>
                <td>
                  {row.health_flag && (
                    <Badge bg="danger" className="me-1">
                      Health
                    </Badge>
                  )}
                  {row.next_task ? <NavLink to={row.next_task.url}>{row.next_task.title}</NavLink> : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
      <div className="d-flex align-items-center gap-3">
        <ButtonGroup>
          <Button variant="outline-secondary" disabled={page <= 1} onClick={() => setPage(page - 1)}>
            Previous
          </Button>
          <Button variant="outline-secondary" disabled={page >= lastPage} onClick={() => setPage(page + 1)}>
            Next
          </Button>
        </ButtonGroup>
        <span className="text-muted">
          Page {page} of {lastPage}
        </span>
      </div>
    </main>
  )
}

function GardenRegisterDetailView() {
  const { registerKey = '' } = useParams()
  const { data, isPending } = useQuery({
    queryKey: queryKeys.plantings.gardenRegisterDetail(registerKey),
    queryFn: ({ signal }) => getGardenRegisterDetail(registerKey, signal),
    enabled: registerKey !== ''
  })
  const statusMutation = useMutation({
    mutationFn: ({ type, reason }: { type: 'finished' | 'failed'; reason: string }) => finishGardenRegisterCrop(registerKey, type, reason),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.plantings.gardenRegisterAll })
    }
  })
  const correctionMutation = useMutation({
    mutationFn: ({ event, reason }: { event: number; reason: string }) => correctGardenRegisterStatus(registerKey, event, reason),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.plantings.gardenRegisterAll })
    }
  })
  if (isPending || !data) return <main className="container py-3">Loading planting…</main>
  const latestStatus = [...data.history].reverse().find((event) => event.type === 'finished' || event.type === 'failed')
  return (
    <main className="container py-3">
      <NavLink to="/plantings/garden-register">← What’s growing</NavLink>
      <h1 className="mt-2">{data.name || data.plant_name}</h1>
      <p>
        {data.variety_name} ·{' '}
        {data.record_type === 'aggregate' ? `${data.quantity_is_approximate ? 'About ' : ''}${data.quantity} in a crop or patch` : 'Individually tracked plant'} ·{' '}
        {data.location_label}
      </p>
      <div className="d-flex flex-wrap gap-2 mb-3">
        <NavLink className="btn btn-outline-primary" to={data.links.garden || '/gardens'}>
          Garden plan
        </NavLink>
        <NavLink className="btn btn-outline-primary" to={data.links.harvest || '/plantings/harvests'}>
          Record harvest
        </NavLink>
        <NavLink className="btn btn-outline-primary" to={data.links.care || '/applications'}>
          Record care
        </NavLink>
        <NavLink className="btn btn-outline-primary" to={data.links.health || '/health'}>
          Health
        </NavLink>
        {data.links.plant && (
          <NavLink className="btn btn-outline-primary" to={data.links.plant}>
            Move, fail or correct
          </NavLink>
        )}
        <NavLink className="btn btn-outline-secondary" to={data.links.batch || '#'}>
          Advanced planting cycle
        </NavLink>
      </div>
      {data.key.startsWith('aggregate-') && data.state === 'current' && (
        <ButtonGroup className="mb-3">
          <Button disabled={statusMutation.isPending} onClick={() => statusMutation.mutate({ type: 'finished', reason: 'Finished from garden register' })}>
            Finish
          </Button>
          <Button
            variant="outline-danger"
            disabled={statusMutation.isPending}
            onClick={() => statusMutation.mutate({ type: 'failed', reason: 'Recorded as failed from garden register' })}
          >
            Record failure
          </Button>
        </ButtonGroup>
      )}
      {data.key.startsWith('aggregate-') && data.state !== 'current' && latestStatus && (
        <Button
          className="mb-3"
          variant="outline-secondary"
          disabled={correctionMutation.isPending}
          onClick={() => correctionMutation.mutate({ event: latestStatus.id, reason: 'Corrected from garden register' })}
        >
          Correct status
        </Button>
      )}
      <Row className="g-3">
        <Col lg={6}>
          <Card body>
            <h2 className="h5">Identity and origin</h2>
            <dl>
              <dt>Source</dt>
              <dd>{sourceLabel(data.source)}</dd>
              <dt>Planted or first seen</dt>
              <dd>{formatDate(data.planted_on)}</dd>
              <dt>Expected harvest</dt>
              <dd>{formatDateRange(data.expected_harvest_early, data.expected_harvest_late) || 'Not estimated'}</dd>
              <dt>Cycle</dt>
              <dd>{data.batch_code}</dd>
            </dl>
            {data.origin?.notes && <p>{data.origin.notes}</p>}
          </Card>
        </Col>
        <Col lg={6}>
          <Card body>
            <h2 className="h5">History</h2>
            {data.history.length === 0 ? (
              <p>No aggregate status changes yet. Individual care, locations, photos and lifecycle history are on the plant record.</p>
            ) : (
              <ol>
                {data.history.map((event) => (
                  <li key={event.id}>
                    {formatDate(event.occurred_on)} · {event.type}
                    {event.reason ? ` — ${event.reason}` : ''}
                  </li>
                ))}
              </ol>
            )}
          </Card>
        </Col>
      </Row>
    </main>
  )
}

export { GardenRegisterDetailView, GardenRegisterView }
