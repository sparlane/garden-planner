import React from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Accordion, Alert, Badge, Button, Card, Col, Form, Row, Table } from 'react-bootstrap'

import { getLocations } from '../api/locations'
import {
  addPlanDemand,
  addPlanningAssumption,
  addPlanningStageAssumption,
  addProductionPlan,
  getGrowthStages,
  getPlanningAssumptions,
  getPlanVariance,
  getProductionPlans,
  postPlanAction
} from '../api/plantings'
import { getPlantVarieties } from '../api/plants'
import { queryKeys } from '../query'
import { NurseryPlanningAssumption, NurseryProductionPlan } from '../types/plantings'
import { PlantVariety } from '../types/plants'

function AssumptionForm({ varieties }: { varieties: Array<PlantVariety> }) {
  const cache = useQueryClient()
  const [variety, setVariety] = React.useState('')
  const [effectiveFrom, setEffectiveFrom] = React.useState('')
  const [germinationRate, setGerminationRate] = React.useState('0.85')
  const [seedsPerCluster, setSeedsPerCluster] = React.useState('1')
  const [trayDensity, setTrayDensity] = React.useState('50')
  const create = useMutation({
    mutationFn: () =>
      addPlanningAssumption({
        variety: Number(variety),
        effective_from: effectiveFrom,
        germination_rate: germinationRate,
        seeds_per_cluster: Number(seedsPerCluster),
        tray_density: Number(trayDensity)
      }),
    onSuccess: () => {
      setVariety('')
      cache.invalidateQueries({ queryKey: queryKeys.plantings.planningAssumptions })
    }
  })
  return (
    <Form
      onSubmit={(event) => {
        event.preventDefault()
        create.mutate()
      }}
    >
      <Row className="g-2 align-items-end">
        <Col md={3}>
          <Form.Label>Variety</Form.Label>
          <Form.Select required value={variety} onChange={(event) => setVariety(event.target.value)}>
            <option value="">Choose…</option>
            {varieties.map((value) => (
              <option key={value.pk} value={value.pk}>
                {value.name}
              </option>
            ))}
          </Form.Select>
        </Col>
        <Col md={2}>
          <Form.Label>Effective from</Form.Label>
          <Form.Control required type="date" value={effectiveFrom} onChange={(event) => setEffectiveFrom(event.target.value)} />
        </Col>
        <Col md={2}>
          <Form.Label>Germination rate</Form.Label>
          <Form.Control required type="number" min="0.000001" max="1" step="0.01" value={germinationRate} onChange={(event) => setGerminationRate(event.target.value)} />
        </Col>
        <Col md={2}>
          <Form.Label>Seeds / cluster</Form.Label>
          <Form.Control required type="number" min="1" value={seedsPerCluster} onChange={(event) => setSeedsPerCluster(event.target.value)} />
        </Col>
        <Col md={2}>
          <Form.Label>Clusters / tray</Form.Label>
          <Form.Control required type="number" min="1" value={trayDensity} onChange={(event) => setTrayDensity(event.target.value)} />
        </Col>
        <Col md="auto">
          <Button type="submit" disabled={create.isPending}>
            Add
          </Button>
        </Col>
      </Row>
    </Form>
  )
}

function StageAssumptionForm({ assumption }: { assumption: NurseryPlanningAssumption }) {
  const cache = useQueryClient()
  const stages = useQuery({ queryKey: ['growth-stages'], queryFn: ({ signal }) => getGrowthStages(signal) })
  const locations = useQuery({ queryKey: queryKeys.locations.list('active'), queryFn: ({ signal }) => getLocations(signal, true) })
  const [stage, setStage] = React.useState('')
  const [leadDays, setLeadDays] = React.useState('7')
  const [lossRate, setLossRate] = React.useState('0.05')
  const [location, setLocation] = React.useState('')
  const [basis, setBasis] = React.useState('plants')
  const create = useMutation({
    mutationFn: () =>
      addPlanningStageAssumption({
        assumption: assumption.pk,
        stage: Number(stage),
        sequence: assumption.stages.length + 1,
        lead_days: Number(leadDays),
        loss_rate: lossRate,
        location: location ? Number(location) : null,
        capacity_basis: basis,
        capacity_per_plant: '1'
      }),
    onSuccess: () => cache.invalidateQueries({ queryKey: queryKeys.plantings.planningAssumptions })
  })
  const used = new Set(assumption.stages.map((row) => row.stage))
  return (
    <Form
      className="border-top pt-2 mt-2"
      onSubmit={(event) => {
        event.preventDefault()
        create.mutate()
      }}
    >
      <Row className="g-2 align-items-end">
        <Col md={3}>
          <Form.Label>Add stage</Form.Label>
          <Form.Select required value={stage} onChange={(event) => setStage(event.target.value)}>
            <option value="">Choose…</option>
            {(stages.data ?? [])
              .filter((value) => !used.has(value.pk))
              .map((value) => (
                <option key={value.pk} value={value.pk}>
                  {value.name}
                </option>
              ))}
          </Form.Select>
        </Col>
        <Col md={2}>
          <Form.Label>Lead days</Form.Label>
          <Form.Control required type="number" min="0" value={leadDays} onChange={(event) => setLeadDays(event.target.value)} />
        </Col>
        <Col md={2}>
          <Form.Label>Loss rate</Form.Label>
          <Form.Control required type="number" min="0" max="0.999999" step="0.01" value={lossRate} onChange={(event) => setLossRate(event.target.value)} />
        </Col>
        <Col md={3}>
          <Form.Label>Location</Form.Label>
          <Form.Select value={location} onChange={(event) => setLocation(event.target.value)}>
            <option value="">Not assigned</option>
            {(locations.data ?? []).map((value) => (
              <option key={value.pk} value={value.pk}>
                {value.full_name}
              </option>
            ))}
          </Form.Select>
        </Col>
        <Col md={1}>
          <Form.Label>Basis</Form.Label>
          <Form.Select value={basis} onChange={(event) => setBasis(event.target.value)}>
            <option value="plants">Plants</option>
            <option value="trays">Trays</option>
            <option value="containers">Pots</option>
            <option value="area">Area</option>
          </Form.Select>
        </Col>
        <Col md="auto">
          <Button type="submit" size="sm" disabled={create.isPending}>
            Add
          </Button>
        </Col>
      </Row>
    </Form>
  )
}

function Assumptions({ values, varieties }: { values: Array<NurseryPlanningAssumption>; varieties: Array<PlantVariety> }) {
  return (
    <Card className="mb-3">
      <Card.Header>Yield and stage assumptions</Card.Header>
      <Card.Body>
        <AssumptionForm varieties={varieties} />
        <Accordion className="mt-3">
          {values.map((value) => (
            <Accordion.Item eventKey={String(value.pk)} key={value.pk}>
              <Accordion.Header>
                {value.variety_name} · from {value.effective_from} · {Number(value.germination_rate) * 100}% germination
              </Accordion.Header>
              <Accordion.Body>
                <div>
                  {value.seeds_per_cluster} seed(s) per cluster · {value.tray_density} clusters per tray
                </div>
                {value.stages.length === 0 ? (
                  <Alert variant="warning" className="mt-2 mb-0">
                    Add at least one stage before calculating demand.
                  </Alert>
                ) : (
                  <Table size="sm" className="mt-2 mb-0">
                    <thead>
                      <tr>
                        <th>Stage</th>
                        <th>Lead</th>
                        <th>Loss</th>
                        <th>Location</th>
                        <th>Capacity</th>
                      </tr>
                    </thead>
                    <tbody>
                      {value.stages.map((stage) => (
                        <tr key={stage.pk}>
                          <td>{stage.stage_name}</td>
                          <td>{stage.lead_days} days</td>
                          <td>{Number(stage.loss_rate) * 100}%</td>
                          <td>{stage.location_name ?? 'Not assigned'}</td>
                          <td>
                            {stage.capacity_per_plant} {stage.capacity_basis} / plant
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </Table>
                )}
                <StageAssumptionForm assumption={value} />
              </Accordion.Body>
            </Accordion.Item>
          ))}
        </Accordion>
      </Card.Body>
    </Card>
  )
}

function NewPlanForm() {
  const cache = useQueryClient()
  const [code, setCode] = React.useState('')
  const [direction, setDirection] = React.useState('backward')
  const [sowingDate, setSowingDate] = React.useState('')
  const create = useMutation({
    mutationFn: () => addProductionPlan({ code, direction, sowing_date: direction === 'forward' ? sowingDate : null }),
    onSuccess: () => {
      setCode('')
      cache.invalidateQueries({ queryKey: queryKeys.plantings.productionPlans })
    }
  })
  return (
    <Card className="mb-3">
      <Card.Header>New plan</Card.Header>
      <Card.Body>
        <Form
          onSubmit={(event) => {
            event.preventDefault()
            create.mutate()
          }}
        >
          <Row className="g-2 align-items-end">
            <Col md={4}>
              <Form.Label>Plan code</Form.Label>
              <Form.Control required value={code} onChange={(event) => setCode(event.target.value)} />
            </Col>
            <Col md={3}>
              <Form.Label>Schedule</Form.Label>
              <Form.Select value={direction} onChange={(event) => setDirection(event.target.value)}>
                <option value="backward">Backward from ready date</option>
                <option value="forward">Forward from sowing date</option>
              </Form.Select>
            </Col>
            {direction === 'forward' && (
              <Col md={3}>
                <Form.Label>Sowing date</Form.Label>
                <Form.Control required type="date" value={sowingDate} onChange={(event) => setSowingDate(event.target.value)} />
              </Col>
            )}
            <Col md="auto">
              <Button type="submit" disabled={create.isPending}>
                Create plan
              </Button>
            </Col>
          </Row>
        </Form>
      </Card.Body>
    </Card>
  )
}

function DemandForm({ plan, varieties }: { plan: NurseryProductionPlan; varieties: Array<PlantVariety> }) {
  const cache = useQueryClient()
  const [variety, setVariety] = React.useState('')
  const [quantity, setQuantity] = React.useState('')
  const [readyFrom, setReadyFrom] = React.useState('')
  const [readyUntil, setReadyUntil] = React.useState('')
  const [source, setSource] = React.useState('manual')
  const [orderReference, setOrderReference] = React.useState('')
  const create = useMutation({
    mutationFn: () =>
      addPlanDemand({
        plan: plan.pk,
        variety: Number(variety),
        target_quantity: Number(quantity),
        ready_from: readyFrom,
        ready_until: readyUntil,
        source,
        priority: 20,
        order_reference: orderReference
      }),
    onSuccess: () => {
      setQuantity('')
      cache.invalidateQueries({ queryKey: queryKeys.plantings.productionPlans })
    }
  })
  return (
    <Form
      className="border-top pt-3"
      onSubmit={(event) => {
        event.preventDefault()
        create.mutate()
      }}
    >
      <Row className="g-2 align-items-end">
        <Col md={3}>
          <Form.Label>Variety</Form.Label>
          <Form.Select required value={variety} onChange={(event) => setVariety(event.target.value)}>
            <option value="">Choose…</option>
            {varieties.map((value) => (
              <option key={value.pk} value={value.pk}>
                {value.name}
              </option>
            ))}
          </Form.Select>
        </Col>
        <Col md={1}>
          <Form.Label>Quantity</Form.Label>
          <Form.Control required type="number" min="1" value={quantity} onChange={(event) => setQuantity(event.target.value)} />
        </Col>
        <Col md={2}>
          <Form.Label>Ready from</Form.Label>
          <Form.Control
            required
            type="date"
            value={readyFrom}
            onChange={(event) => {
              setReadyFrom(event.target.value)
              if (!readyUntil) setReadyUntil(event.target.value)
            }}
          />
        </Col>
        <Col md={2}>
          <Form.Label>Ready until</Form.Label>
          <Form.Control required type="date" min={readyFrom} value={readyUntil} onChange={(event) => setReadyUntil(event.target.value)} />
        </Col>
        <Col md={2}>
          <Form.Label>Source</Form.Label>
          <Form.Select value={source} onChange={(event) => setSource(event.target.value)}>
            <option value="manual">Manual</option>
            <option value="forecast">Forecast</option>
            <option value="confirmed_order">Confirmed order</option>
          </Form.Select>
        </Col>
        {source === 'confirmed_order' && (
          <Col md={2}>
            <Form.Label>Order reference</Form.Label>
            <Form.Control required value={orderReference} onChange={(event) => setOrderReference(event.target.value)} />
          </Col>
        )}
        <Col md="auto">
          <Button size="sm" type="submit" disabled={create.isPending}>
            Add demand
          </Button>
        </Col>
      </Row>
    </Form>
  )
}

function PlanVariance({ plan }: { plan: NurseryProductionPlan }) {
  const variance = useQuery({
    queryKey: queryKeys.plantings.productionPlanVariance(plan.pk),
    queryFn: ({ signal }) => getPlanVariance(plan.pk, signal),
    enabled: plan.status === 'approved'
  })
  if (plan.status !== 'approved' || !variance.data?.length) return null
  return (
    <Table size="sm" className="mt-3">
      <thead>
        <tr>
          <th>Batch</th>
          <th>Seeds</th>
          <th>Germinated</th>
          <th>Losses</th>
          <th>Output / available</th>
          <th>Start</th>
          <th>Ready</th>
        </tr>
      </thead>
      <tbody>
        {variance.data.map((row) => (
          <tr key={row.demand}>
            <td>
              {row.batch ?? '—'} ({row.batch_status})
            </td>
            <td>
              {row.planned_seeds} / {row.actual_seeds}
            </td>
            <td>
              {row.planned_germinated} / {row.actual_germinated}
            </td>
            <td>
              {row.planned_losses} / {row.actual_losses}
            </td>
            <td>
              {row.planned_output} / {row.current_output} / {row.final_availability}
            </td>
            <td>
              {row.planned_sowing_date} / {row.actual_sowing_date ?? 'not started'}
            </td>
            <td>
              {row.planned_ready_date} / {row.actual_ready_date ?? 'not ready'}
            </td>
          </tr>
        ))}
      </tbody>
    </Table>
  )
}

function PlanCard({ plan, varieties }: { plan: NurseryProductionPlan; varieties: Array<PlantVariety> }) {
  const cache = useQueryClient()
  const action = useMutation({
    mutationFn: (name: 'calculate' | 'approve' | 'revise') => postPlanAction(plan.pk, name),
    onSuccess: () => cache.invalidateQueries({ queryKey: queryKeys.plantings.productionPlans })
  })
  return (
    <Card className="mb-3">
      <Card.Header className="d-flex justify-content-between align-items-center">
        <span>
          {plan.code} v{plan.version} <Badge bg={plan.status === 'approved' ? 'success' : 'secondary'}>{plan.status}</Badge>
        </span>
        <span>
          {plan.status === 'draft' ? (
            <>
              <Button size="sm" variant="outline-primary" className="me-2" disabled={action.isPending || plan.demand_lines.length === 0} onClick={() => action.mutate('calculate')}>
                Calculate
              </Button>
              <Button size="sm" variant="success" disabled={action.isPending || plan.demand_lines.some((line) => !line.requirement)} onClick={() => action.mutate('approve')}>
                Approve
              </Button>
            </>
          ) : (
            <Button size="sm" variant="outline-primary" disabled={action.isPending} onClick={() => action.mutate('revise')}>
              New version
            </Button>
          )}
        </span>
      </Card.Header>
      <Card.Body>
        {plan.issues.map((issue) => (
          <Alert key={issue.pk} variant="warning" className="py-2">
            {issue.kind}: {issue.message}
          </Alert>
        ))}
        {plan.demand_lines.length > 0 && (
          <Table responsive size="sm">
            <thead>
              <tr>
                <th>Demand</th>
                <th>Ready</th>
                <th>Source</th>
                <th>Seeds / trays</th>
                <th>Milestones</th>
                <th>Batch</th>
              </tr>
            </thead>
            <tbody>
              {plan.demand_lines.map((line) => (
                <tr key={line.pk}>
                  <td>
                    {line.target_quantity} × {line.variety_name}
                  </td>
                  <td>
                    {line.ready_from}–{line.ready_until}
                  </td>
                  <td>{line.source.replace('_', ' ')}</td>
                  <td>{line.requirement ? `${line.requirement.required_seeds} / ${line.requirement.required_trays}` : 'Calculate plan'}</td>
                  <td>
                    {line.requirement?.milestones.map((milestone) => (
                      <div key={milestone.pk}>
                        {milestone.planned_date}: {milestone.stage_name} ({milestone.input_quantity} → {milestone.expected_output})
                      </div>
                    ))}
                  </td>
                  <td>{line.requirement?.batch ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
        {plan.status === 'draft' && <DemandForm plan={plan} varieties={varieties} />}
        <PlanVariance plan={plan} />
      </Card.Body>
    </Card>
  )
}

function ProductionPlanningView() {
  const varieties = useQuery({ queryKey: queryKeys.plants.varieties, queryFn: ({ signal }) => getPlantVarieties(signal) })
  const assumptions = useQuery({ queryKey: queryKeys.plantings.planningAssumptions, queryFn: ({ signal }) => getPlanningAssumptions(signal) })
  const plans = useQuery({ queryKey: queryKeys.plantings.productionPlans, queryFn: ({ signal }) => getProductionPlans(signal) })
  return (
    <main className="container py-3">
      <h1>Nursery production planning</h1>
      <p>Turn committed, forecast, and manual demand into reviewed sowing, stage, stock, and capacity requirements.</p>
      <Assumptions values={assumptions.data ?? []} varieties={varieties.data ?? []} />
      <NewPlanForm />
      {(plans.data ?? []).map((plan) => (
        <PlanCard key={plan.pk} plan={plan} varieties={varieties.data ?? []} />
      ))}
    </main>
  )
}

export { ProductionPlanningView }
