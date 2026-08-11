import React from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Card, Col, Form, Row, Table } from 'react-bootstrap'

import { getGrowthStages, getPlantGrades, saveGrowthCatalog } from '../api/plantings'
import { GrowthCatalogValue } from '../types/plantings'

function Catalog({ kind, values }: { kind: 'growth-stages' | 'plant-grades'; values: Array<GrowthCatalogValue> }) {
  const cache = useQueryClient()
  const [code, setCode] = React.useState('')
  const [name, setName] = React.useState('')
  const mutation = useMutation({
    mutationFn: (value: Partial<GrowthCatalogValue> & { name: string; code?: string }) => saveGrowthCatalog(kind, value),
    onSuccess: () => cache.invalidateQueries({ queryKey: [kind] })
  })
  return (
    <Card>
      <Card.Header>{kind === 'growth-stages' ? 'Growth stages' : 'Plant grades'}</Card.Header>
      <Card.Body>
        <Table size="sm">
          <thead>
            <tr>
              <th>Name</th>
              <th>Code</th>
              <th>Target days</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {values.map((value) => (
              <tr key={value.pk}>
                <td>{value.name}</td>
                <td>{value.code}</td>
                <td>
                  {kind === 'growth-stages' ? (
                    <Form.Control
                      aria-label={`Target days for ${value.name}`}
                      type="number"
                      min={1}
                      defaultValue={value.target_days ?? ''}
                      onBlur={(event) => {
                        const targetDays = event.target.value === '' ? null : Number(event.target.value)
                        if (targetDays !== value.target_days) mutation.mutate({ pk: value.pk, name: value.name, target_days: targetDays })
                      }}
                    />
                  ) : (
                    '—'
                  )}
                </td>
                <td>
                  <Button size="sm" variant="outline-secondary" onClick={() => mutation.mutate({ pk: value.pk, name: value.name, active: !value.active })}>
                    {value.active ? 'Deactivate' : 'Activate'}
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
        <Row className="g-2">
          <Col>
            <Form.Control placeholder="stable_code" value={code} onChange={(event) => setCode(event.target.value)} />
          </Col>
          <Col>
            <Form.Control placeholder="Display name" value={name} onChange={(event) => setName(event.target.value)} />
          </Col>
          <Col xs="auto">
            <Button disabled={!code || !name} onClick={() => mutation.mutate({ code, name, display_order: values.length })}>
              Add
            </Button>
          </Col>
        </Row>
      </Card.Body>
    </Card>
  )
}

function GrowthCatalogsView() {
  const stages = useQuery({ queryKey: ['growth-stages'], queryFn: ({ signal }) => getGrowthStages(signal) })
  const grades = useQuery({ queryKey: ['plant-grades'], queryFn: ({ signal }) => getPlantGrades(signal) })
  return (
    <main className="container py-3">
      <h1>Nursery growth setup</h1>
      <p>Maintain the operational stages and commercial grades used by observations and registers.</p>
      <Row className="g-3">
        <Col lg={6}>
          <Catalog kind="growth-stages" values={stages.data ?? []} />
        </Col>
        <Col lg={6}>
          <Catalog kind="plant-grades" values={grades.data ?? []} />
        </Col>
      </Row>
    </main>
  )
}

export { GrowthCatalogsView }
