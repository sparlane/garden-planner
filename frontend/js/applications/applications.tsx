import React from 'react'
import { useQuery } from '@tanstack/react-query'
import { Col, Form, Row } from 'react-bootstrap'

import { getInputApplications } from '../api/applications'
import { getInventoryItems } from '../api/inventory'
import { queryKeys } from '../query'
import { InputApplicationStatus } from '../types/applications'
import { ApplicationTable } from './application_list'

function InputApplicationsView() {
  const [status, setStatus] = React.useState<InputApplicationStatus | ''>('')
  const [item, setItem] = React.useState<number | ''>('')
  const [from, setFrom] = React.useState('')
  const [to, setTo] = React.useState('')

  const { data: items = [] } = useQuery({
    queryKey: queryKeys.inventory.items('', '', '', 'active'),
    queryFn: ({ signal }) => getInventoryItems({ active: true }, signal)
  })
  const { data: applications = [], isPending } = useQuery({
    queryKey: queryKeys.applications.list(status, '', item, from, to),
    queryFn: ({ signal }) => getInputApplications({ status, item, applied_from: from, applied_to: to }, signal)
  })

  return (
    <main className="container py-3">
      <h1>Input applications</h1>
      <p>Every input that left stock, the exact lot it came from, and what it went on.</p>
      <Row className="g-2 mb-3">
        <Col md={3}>
          <Form.Group controlId="application-filter-status">
            <Form.Label>Status</Form.Label>
            <Form.Select value={status} onChange={(event) => setStatus(event.target.value as InputApplicationStatus | '')}>
              <option value="">All</option>
              <option value="draft">Draft</option>
              <option value="posted">Posted</option>
              <option value="reversed">Reversed</option>
            </Form.Select>
          </Form.Group>
        </Col>
        <Col md={3}>
          <Form.Group controlId="application-filter-item">
            <Form.Label>Item</Form.Label>
            <Form.Select value={item} onChange={(event) => setItem(event.target.value === '' ? '' : Number(event.target.value))}>
              <option value="">All</option>
              {items.map((entry) => (
                <option key={entry.pk} value={entry.pk}>
                  {entry.name}
                </option>
              ))}
            </Form.Select>
          </Form.Group>
        </Col>
        <Col md={3}>
          <Form.Group controlId="application-filter-from">
            <Form.Label>From</Form.Label>
            <Form.Control type="date" value={from} onChange={(event) => setFrom(event.target.value)} />
          </Form.Group>
        </Col>
        <Col md={3}>
          <Form.Group controlId="application-filter-to">
            <Form.Label>To</Form.Label>
            <Form.Control type="date" value={to} onChange={(event) => setTo(event.target.value)} />
          </Form.Group>
        </Col>
      </Row>
      {isPending ? <div>Loading applications…</div> : <ApplicationTable applications={applications} />}
    </main>
  )
}

export { InputApplicationsView }
