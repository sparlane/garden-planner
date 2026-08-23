import React from 'react'
import { useQuery } from '@tanstack/react-query'
import { Alert, Badge, Button, Card, Col, Row, Table } from 'react-bootstrap'
import { Link, useParams } from 'react-router'

import { getPrintableDocument, getSupplyDocuments } from '../api/billing'
import { queryKeys } from '../query'
import { PrintableCorrection, PrintableDocument, PrintableParty, SupplyDocumentStatus } from '../types/billing'
import { formatDate, formatMoney } from '../utils'
import '../billing.css'

const STATUS_LABELS: Record<SupplyDocumentStatus, string> = {
  issued: 'Issued',
  part_credited: 'Part credited',
  credited: 'Credited in full'
}

const STATUS_VARIANTS: Record<SupplyDocumentStatus, string> = {
  issued: 'success',
  part_credited: 'warning',
  credited: 'secondary'
}

const CORRECTION_LABELS: Record<string, string> = {
  return: 'Goods returned',
  discount: 'Discount or price adjustment',
  wrong_rate: 'Wrong GST rate or treatment',
  cancellation: 'Supply cancelled',
  partial_credit: 'Part of the supply credited',
  other: 'Other'
}

function documentStatusBadge(status: SupplyDocumentStatus) {
  return <Badge bg={STATUS_VARIANTS[status]}>{STATUS_LABELS[status]}</Badge>
}

function PartyBlock({ heading, party }: { heading: string; party: PrintableParty }) {
  const name = party.legal_name ?? party.name ?? ''
  return (
    <div>
      <div className="text-muted text-uppercase small">{heading}</div>
      <div className="fw-bold">{name || 'Not recorded'}</div>
      {party.trading_name && <div>Trading as {party.trading_name}</div>}
      <div className="supply-document__party">{party.address}</div>
      {party.gst_number && <div>GST number {party.gst_number}</div>}
      {party.identifier && <div>{party.identifier}</div>}
    </div>
  )
}

function CorrectionRows({ corrections, currency }: { corrections: Array<PrintableCorrection>; currency: string }) {
  if (corrections.length === 0) return null
  return (
    <>
      <h2 className="h6 mt-4">Corrections to this document</h2>
      <Table size="sm" className="mb-0">
        <thead>
          <tr>
            <th>Number</th>
            <th>Date</th>
            <th>Kind</th>
            <th>Reason</th>
            <th className="text-end">GST</th>
            <th className="text-end">Total</th>
          </tr>
        </thead>
        <tbody>
          {corrections.map((correction) => (
            <tr key={correction.pk}>
              <td>{correction.document_number}</td>
              <td>{formatDate(correction.corrected_on)}</td>
              <td>{correction.correction_type === 'credit' ? 'Credit note' : 'Debit note'}</td>
              <td>
                {CORRECTION_LABELS[correction.reason_code] ?? correction.reason_code}
                {correction.reason && <div className="text-muted small">{correction.reason}</div>}
              </td>
              <td className="text-end">{formatMoney(correction.tax_total, currency)}</td>
              <td className="text-end">{formatMoney(correction.total_incl_tax, currency)}</td>
            </tr>
          ))}
        </tbody>
      </Table>
    </>
  )
}

function RequirementChecklist({ document }: { document: PrintableDocument }) {
  const missing = document.required_information.filter((element) => !element.satisfied)
  return (
    <Card body className="mt-3 no-print">
      <div className="fw-bold">Required information for a supply of {document.tier_label.toLowerCase()}</div>
      <ul className="mb-0">
        {document.required_information.map((element) => (
          <li key={element.code} className={element.satisfied ? '' : 'supply-document__requirement--missing'}>
            {element.label}
            {element.satisfied ? '' : ' — missing'}
          </li>
        ))}
      </ul>
      {missing.length > 0 && (
        <Alert variant="danger" className="mt-2 mb-0">
          This document no longer states everything its value band requires. Issuing refuses an incomplete document, so this one was written by something other than the issuing
          service.
        </Alert>
      )}
    </Card>
  )
}

function DocumentTotals({ document }: { document: PrintableDocument }) {
  const currency = document.currency_code
  const totals = document.totals
  // A deposit is not a line on the document; it is money already received, so
  // it shows here as what has been paid rather than as a supply of its own.
  const rows: Array<[string, string]> = [
    ['Subtotal excluding GST', totals.subtotal_ex_tax],
    [document.taxable_supply ? 'GST' : 'Tax', totals.tax_total],
    ['Total for this document', totals.total_incl_tax],
    ['Previously invoiced on this order', totals.previously_invoiced],
    ['Paid to date', totals.paid_to_date],
    ['Balance due', totals.balance_due]
  ]
  if (totals.overpaid_at_issue !== '0.0000') rows.push(['Paid beyond everything invoiced', totals.overpaid_at_issue])
  if (totals.credited_total !== '0.0000') {
    rows.push(['Credited since issue', totals.credited_total])
    rows.push(['Net of corrections', totals.net_total_incl_tax])
  }
  return (
    <Table size="sm" className="w-auto ms-auto mt-3">
      <tbody>
        {rows.map(([label, value]) => (
          <tr key={label}>
            <th className="pe-4">{label}</th>
            <td className="text-end">{formatMoney(value, currency)}</td>
          </tr>
        ))}
      </tbody>
    </Table>
  )
}

function SupplyDocumentPrintView() {
  const params = useParams()
  const pk = Number(params.documentId)
  const query = useQuery({ queryKey: queryKeys.billing.printable(pk), queryFn: ({ signal }) => getPrintableDocument(pk, signal) })
  const document = query.data
  if (!document) return <main className="container py-3">Loading document…</main>
  const currency = document.currency_code
  return (
    <main className="container py-3">
      <div className="d-flex justify-content-between align-items-center no-print mb-3">
        <Link to="/sales/documents">Back to documents</Link>
        <Button onClick={() => window.print()}>Print</Button>
      </div>
      <Card body className="supply-document mx-auto">
        <div className="d-flex justify-content-between align-items-start">
          <div>
            <h1 className="h4 mb-0">{document.title}</h1>
            <div className="text-muted">{document.document_number}</div>
          </div>
          <div className="text-end">
            <div>Date {formatDate(document.issued_on)}</div>
            <div className="text-muted">Order {document.order_number}</div>
          </div>
        </div>
        <Row className="g-3 mt-2">
          <Col md={6}>
            <PartyBlock heading="Supplied by" party={document.seller} />
          </Col>
          <Col md={6}>
            <PartyBlock heading="Supplied to" party={document.buyer} />
          </Col>
        </Row>
        {!document.taxable_supply && (
          <Alert variant="secondary" className="mt-3 mb-0">
            The supplier was not GST registered on this date, so this is a sales receipt rather than taxable supply information and no GST is charged.
          </Alert>
        )}
        <Table size="sm" className="mt-3">
          <thead>
            <tr>
              <th>Description</th>
              <th className="text-end">Quantity</th>
              <th className="text-end">Unit price</th>
              <th className="text-end">Excluding GST</th>
              <th className="text-end">GST</th>
              <th className="text-end">Total</th>
            </tr>
          </thead>
          <tbody>
            {document.lines.map((line) => (
              <tr key={line.pk}>
                <td>
                  {line.description}
                  <div className="text-muted small">
                    Items {line.positions.join(', ')}
                    {line.dispatched_positions.length > 0 && ` · dispatched ${line.dispatched_positions.join(', ')}`}
                  </div>
                </td>
                <td className="text-end">{line.quantity}</td>
                <td className="text-end">{formatMoney(line.unit_price, currency)}</td>
                <td className="text-end">{formatMoney(line.subtotal_ex_tax, currency)}</td>
                <td className="text-end">{formatMoney(line.tax_total, currency)}</td>
                <td className="text-end">{formatMoney(line.total_incl_tax, currency)}</td>
              </tr>
            ))}
          </tbody>
        </Table>
        <DocumentTotals document={document} />
        {document.notes && <p className="mt-3">{document.notes}</p>}
        <CorrectionRows corrections={document.corrections} currency={currency} />
      </Card>
      <RequirementChecklist document={document} />
    </main>
  )
}

function SupplyDocumentListView() {
  const query = useQuery({ queryKey: queryKeys.billing.documents(''), queryFn: ({ signal }) => getSupplyDocuments({}, signal) })
  const documents = query.data ?? []
  return (
    <main className="container py-3">
      <h1>Taxable supply documents</h1>
      <p>
        Every invoice and receipt issued to a customer, newest first. A document is never edited: correcting one means issuing a credit or debit note against it, and both stay
        readable here.
      </p>
      {documents.length === 0 && <Alert variant="secondary">No documents have been issued yet. Issue one from a confirmed order.</Alert>}
      {documents.length > 0 && (
        <Table hover responsive>
          <thead>
            <tr>
              <th>Number</th>
              <th>Date</th>
              <th>Order</th>
              <th>Customer</th>
              <th className="text-end">Total</th>
              <th className="text-end">Net of corrections</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {documents.map((document) => (
              <tr key={document.pk}>
                <td>
                  <Link to={`/sales/documents/${document.pk}`}>{document.document_number}</Link>
                  {!document.taxable_supply && <div className="text-muted small">Not a taxable supply</div>}
                </td>
                <td>{formatDate(document.issued_on)}</td>
                <td>
                  <Link to={`/sales/orders/${document.order}`}>{document.order_number}</Link>
                </td>
                <td>{document.buyer_name || 'Walk-in'}</td>
                <td className="text-end">{formatMoney(document.total_incl_tax, document.currency_code)}</td>
                <td className="text-end">{formatMoney(document.state.net_total_incl_tax, document.currency_code)}</td>
                <td>{documentStatusBadge(document.state.status)}</td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </main>
  )
}

export { CORRECTION_LABELS, SupplyDocumentListView, SupplyDocumentPrintView, documentStatusBadge }
