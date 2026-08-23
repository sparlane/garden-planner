import React from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Alert, Button, Card, Col, Form, Row, Table } from 'react-bootstrap'
import { Link } from 'react-router'

import { getInvoiceable, getSupplyDocuments, issueCorrection, issueSupplyDocument } from '../api/billing'
import { queryClient, queryKeys } from '../query'
import { CorrectionReason, CorrectionType, InvoiceableLine, SupplyDocument } from '../types/billing'
import { SalesOrder } from '../types/sales'
import { errorsByField, formatDate, formatMoney, sumMoney } from '../utils'
import { CORRECTION_LABELS, documentStatusBadge } from './documents'

const CORRECTION_REASONS: Array<CorrectionReason> = ['return', 'discount', 'wrong_rate', 'cancellation', 'partial_credit', 'other']

function positionKey(orderLine: number, position: number) {
  return `${orderLine}:${position}`
}

function selectedLines(selected: Set<string>, lines: Array<InvoiceableLine>) {
  return lines
    .map((line) => ({
      order_line: line.order_line,
      positions: line.positions.map((item) => item.position).filter((position) => selected.has(positionKey(line.order_line, position)))
    }))
    .filter((line) => line.positions.length > 0)
}

function IssueForm({ order, onIssued }: { order: SalesOrder; onIssued: () => void }) {
  const invoiceable = useQuery({ queryKey: queryKeys.billing.invoiceable(order.pk), queryFn: ({ signal }) => getInvoiceable(order.pk, signal) })
  const [selected, setSelected] = React.useState<Set<string>>(new Set())
  const [issuedOn, setIssuedOn] = React.useState('')
  const [buyerName, setBuyerName] = React.useState('')
  const [buyerIdentifier, setBuyerIdentifier] = React.useState('')
  const [notes, setNotes] = React.useState('')
  const lines = invoiceable.data?.lines ?? []
  const available = lines.flatMap((line) => line.positions.map((item) => positionKey(line.order_line, item.position)))
  const chosen = selectedLines(selected, lines)
  const total = sumMoney(lines.flatMap((line) => line.positions.filter((item) => selected.has(positionKey(line.order_line, item.position))).map((item) => item.total_incl_tax)))

  const issue = useMutation({
    mutationFn: () =>
      issueSupplyDocument({
        operation_key: crypto.randomUUID(),
        order: order.pk,
        lines: chosen,
        ...(issuedOn ? { issued_on: issuedOn } : {}),
        ...(buyerName || buyerIdentifier ? { buyer: { buyer_name: buyerName, buyer_identifier: buyerIdentifier } } : {}),
        notes
      }),
    onSuccess: () => {
      setSelected(new Set())
      setNotes('')
      onIssued()
    }
  })
  const fieldErrors = errorsByField(issue.error)

  function toggle(key: string) {
    setSelected((current) => {
      const next = new Set(current)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  if (available.length === 0) {
    return <Alert variant="secondary">Every item on this order has been invoiced.</Alert>
  }
  return (
    <Form
      onSubmit={(event) => {
        event.preventDefault()
        issue.mutate()
      }}
    >
      {lines.map((line) => (
        <div className="mb-2" key={line.order_line}>
          <div className="fw-bold">{line.description}</div>
          {line.invoiced_positions.length > 0 && <div className="text-muted small">Already invoiced: items {line.invoiced_positions.join(', ')}</div>}
          {line.returned_positions.length > 0 && <div className="text-muted small">Returned before invoicing: items {line.returned_positions.join(', ')}</div>}
          <div className="d-flex flex-wrap gap-3">
            {line.positions.map((item) => (
              <Form.Check
                key={item.position}
                id={`invoice-${line.order_line}-${item.position}`}
                label={`Item ${item.position} · ${formatMoney(item.total_incl_tax, order.currency_code)}${item.dispatched ? '' : ' · not yet dispatched'}`}
                checked={selected.has(positionKey(line.order_line, item.position))}
                onChange={() => toggle(positionKey(line.order_line, item.position))}
              />
            ))}
          </div>
        </div>
      ))}
      <div className="mb-2">
        <Button size="sm" variant="outline-secondary" onClick={() => setSelected(new Set(available))} type="button">
          Select everything invoiceable
        </Button>
      </div>
      <Row className="g-2">
        <Col md={3}>
          <Form.Label>Document date</Form.Label>
          <Form.Control type="date" value={issuedOn} onChange={(event) => setIssuedOn(event.target.value)} isInvalid={'issued_on' in fieldErrors} />
          <Form.Control.Feedback type="invalid">{fieldErrors.issued_on}</Form.Control.Feedback>
          <Form.Text>Left blank, today. This is the date the invoice basis recognises the supply on.</Form.Text>
        </Col>
        <Col md={3}>
          <Form.Label>Buyer name</Form.Label>
          <Form.Control value={buyerName} onChange={(event) => setBuyerName(event.target.value)} isInvalid={'buyer_name' in fieldErrors} />
          <Form.Control.Feedback type="invalid">{fieldErrors.buyer_name}</Form.Control.Feedback>
          <Form.Text>Only needed above $1,000 when the order has no customer.</Form.Text>
        </Col>
        <Col md={3}>
          <Form.Label>Buyer address, NZBN, email or phone</Form.Label>
          <Form.Control value={buyerIdentifier} onChange={(event) => setBuyerIdentifier(event.target.value)} isInvalid={'buyer_identification' in fieldErrors} />
          <Form.Control.Feedback type="invalid">{fieldErrors.buyer_identification}</Form.Control.Feedback>
        </Col>
        <Col md={3}>
          <Form.Label>Notes</Form.Label>
          <Form.Control value={notes} onChange={(event) => setNotes(event.target.value)} />
        </Col>
      </Row>
      {fieldErrors.seller_legal_name && (
        <Alert variant="danger" className="mt-2">
          {fieldErrors.seller_legal_name} <Link to="/settings">Open workspace settings</Link>
        </Alert>
      )}
      {fieldErrors.order && (
        <Alert variant="danger" className="mt-2">
          {fieldErrors.order}
        </Alert>
      )}
      {fieldErrors.lines && (
        <Alert variant="danger" className="mt-2">
          {fieldErrors.lines}
        </Alert>
      )}
      {fieldErrors.taxable_supply && (
        <Alert variant="danger" className="mt-2">
          {fieldErrors.taxable_supply}
        </Alert>
      )}
      <Button className="mt-2" type="submit" disabled={chosen.length === 0 || issue.isPending}>
        Issue document for {formatMoney(total, order.currency_code)}
      </Button>
    </Form>
  )
}

function CorrectionForm({ document, onIssued }: { document: SupplyDocument; onIssued: () => void }) {
  const [correctionType, setCorrectionType] = React.useState<CorrectionType>('credit')
  const [reasonCode, setReasonCode] = React.useState<CorrectionReason>('return')
  const [reason, setReason] = React.useState('')
  const [full, setFull] = React.useState(false)
  const [documentLine, setDocumentLine] = React.useState<number | ''>(document.lines[0]?.pk ?? '')
  const [amount, setAmount] = React.useState('')
  const correct = useMutation({
    mutationFn: () =>
      issueCorrection(document.pk, {
        operation_key: crypto.randomUUID(),
        correction_type: correctionType,
        reason_code: reasonCode,
        reason,
        ...(full ? { full: true } : { lines: [{ document_line: documentLine as number, amount }] })
      }),
    onSuccess: () => {
      setAmount('')
      setReason('')
      onIssued()
    }
  })
  const fieldErrors = errorsByField(correct.error)
  return (
    <Form
      className="mt-2"
      onSubmit={(event) => {
        event.preventDefault()
        correct.mutate()
      }}
    >
      <Row className="g-2 align-items-end">
        <Col md={2}>
          <Form.Label>Correction</Form.Label>
          <Form.Select value={correctionType} onChange={(event) => setCorrectionType(event.target.value as CorrectionType)}>
            <option value="credit">Credit note</option>
            <option value="debit">Debit note</option>
          </Form.Select>
        </Col>
        <Col md={3}>
          <Form.Label>Reason</Form.Label>
          <Form.Select value={reasonCode} onChange={(event) => setReasonCode(event.target.value as CorrectionReason)}>
            {CORRECTION_REASONS.map((code) => (
              <option key={code} value={code}>
                {CORRECTION_LABELS[code]}
              </option>
            ))}
          </Form.Select>
        </Col>
        <Col md={4}>
          <Form.Label>Detail</Form.Label>
          <Form.Control required value={reason} onChange={(event) => setReason(event.target.value)} isInvalid={'reason' in fieldErrors} />
          <Form.Control.Feedback type="invalid">{fieldErrors.reason}</Form.Control.Feedback>
        </Col>
        <Col md={3}>
          <Form.Check
            id={`full-credit-${document.pk}`}
            label="Credit the whole document"
            checked={full}
            disabled={correctionType === 'debit'}
            onChange={(event) => setFull(event.target.checked)}
          />
        </Col>
      </Row>
      {!full && (
        <Row className="g-2 align-items-end mt-1">
          <Col md={5}>
            <Form.Label>Line</Form.Label>
            <Form.Select value={documentLine} onChange={(event) => setDocumentLine(Number(event.target.value))}>
              {document.lines.map((line) => (
                <option key={line.pk} value={line.pk}>
                  {line.description} · {formatMoney(line.total_incl_tax, document.currency_code)}
                </option>
              ))}
            </Form.Select>
          </Col>
          <Col md={3}>
            <Form.Label>Amount including GST</Form.Label>
            <Form.Control required type="number" min="0" step="0.0001" value={amount} onChange={(event) => setAmount(event.target.value)} />
          </Col>
        </Row>
      )}
      {fieldErrors.lines && (
        <Alert variant="danger" className="mt-2">
          {fieldErrors.lines}
        </Alert>
      )}
      {fieldErrors.corrected_on && (
        <Alert variant="danger" className="mt-2">
          {fieldErrors.corrected_on}
        </Alert>
      )}
      {fieldErrors.document && (
        <Alert variant="danger" className="mt-2">
          {fieldErrors.document}
        </Alert>
      )}
      {fieldErrors.non_field_errors && (
        <Alert variant="danger" className="mt-2">
          {fieldErrors.non_field_errors}
        </Alert>
      )}
      <Button className="mt-2" size="sm" variant="outline-danger" type="submit" disabled={correct.isPending}>
        Issue correction
      </Button>
    </Form>
  )
}

function SupplyDocumentPanel({ order }: { order: SalesOrder }) {
  const documents = useQuery({ queryKey: queryKeys.billing.documents(order.pk), queryFn: ({ signal }) => getSupplyDocuments({ order: order.pk }, signal) })
  const [correcting, setCorrecting] = React.useState<number | null>(null)

  function refresh() {
    void queryClient.invalidateQueries({ queryKey: queryKeys.billing.all })
  }

  return (
    <Card body className="mb-3">
      <h2 className="h5">Taxable supply documents</h2>
      <p className="text-muted mb-2">
        An issued document is never edited. Correcting one means issuing a credit or debit note against it, and crediting a document in full puts its items back on the list below
        so a corrected one can replace it.
      </p>
      {(documents.data ?? []).length > 0 && (
        <Table size="sm" responsive>
          <thead>
            <tr>
              <th>Number</th>
              <th>Date</th>
              <th className="text-end">Total</th>
              <th className="text-end">Net</th>
              <th className="text-end">Balance due</th>
              <th>Status</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {(documents.data ?? []).map((document) => (
              <React.Fragment key={document.pk}>
                <tr>
                  <td>
                    <Link to={`/sales/documents/${document.pk}`}>{document.document_number}</Link>
                  </td>
                  <td>{formatDate(document.issued_on)}</td>
                  <td className="text-end">{formatMoney(document.total_incl_tax, document.currency_code)}</td>
                  <td className="text-end">{formatMoney(document.state.net_total_incl_tax, document.currency_code)}</td>
                  <td className="text-end">{formatMoney(document.balance_due, document.currency_code)}</td>
                  <td>{documentStatusBadge(document.state.status)}</td>
                  <td className="text-end">
                    <Button size="sm" variant="outline-secondary" onClick={() => setCorrecting(correcting === document.pk ? null : document.pk)}>
                      {correcting === document.pk ? 'Close' : 'Correct'}
                    </Button>
                  </td>
                </tr>
                {correcting === document.pk && (
                  <tr>
                    <td colSpan={7}>
                      {document.corrections.length > 0 && (
                        <ul className="mb-2">
                          {document.corrections.map((correction) => (
                            <li key={correction.pk}>
                              {correction.document_number} · {formatDate(correction.corrected_on)} · {CORRECTION_LABELS[correction.reason_code]} ·{' '}
                              {formatMoney(correction.total_incl_tax, correction.currency_code)}
                            </li>
                          ))}
                        </ul>
                      )}
                      <CorrectionForm
                        document={document}
                        onIssued={() => {
                          refresh()
                          setCorrecting(null)
                        }}
                      />
                    </td>
                  </tr>
                )}
              </React.Fragment>
            ))}
          </tbody>
        </Table>
      )}
      <IssueForm order={order} onIssued={refresh} />
    </Card>
  )
}

export { SupplyDocumentPanel }
