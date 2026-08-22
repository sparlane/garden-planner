import React from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Badge, Button, Form, Spinner, Table } from 'react-bootstrap'

import { createGstRegistration, getGstRegistrations, getGstStatus } from '../api/tax'
import { queryKeys } from '../query'
import { GstBasis, GstFrequency, GstRegistration, GstRegistrationCreate } from '../types/tax'
import { errorsByField, formatDate } from '../utils'

const BASIS_LABELS: Record<GstBasis, string> = {
  payments: 'Payments',
  invoice: 'Invoice',
  hybrid: 'Hybrid'
}

const FREQUENCY_LABELS: Record<GstFrequency, string> = {
  monthly: 'Monthly',
  two_monthly: 'Two-monthly',
  six_monthly: 'Six-monthly'
}

const MONTH_NAMES = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']

// An arrangement is appended, never edited, so the form starts empty rather
// than pre-filled from what applies now. Pre-filling would invite an operator
// to treat recording a change as correcting a field.
const EMPTY_FORM = {
  registered: true,
  effective_from: '',
  gst_number: '',
  basis: 'invoice' as GstBasis,
  filing_frequency: 'two_monthly' as GstFrequency,
  period_anchor_month: '3',
  taxable_activity_start: '',
  reason: ''
}

type RegistrationForm = typeof EMPTY_FORM

function describe(registration: GstRegistration): string {
  if (!registration.registered) {
    return 'Not registered'
  }
  const basis = registration.basis ? BASIS_LABELS[registration.basis] : ''
  const frequency = registration.filing_frequency ? FREQUENCY_LABELS[registration.filing_frequency] : ''
  return `${basis} basis, ${frequency}`
}

function GstRegistrationSettings() {
  const queryClient = useQueryClient()
  const [form, setForm] = React.useState<RegistrationForm>(EMPTY_FORM)
  const status = useQuery({ queryKey: queryKeys.tax.gstStatus, queryFn: ({ signal }) => getGstStatus(signal) })
  const history = useQuery({ queryKey: queryKeys.tax.gstRegistrations, queryFn: ({ signal }) => getGstRegistrations(signal) })
  const mutation = useMutation({
    mutationFn: createGstRegistration,
    onSuccess: () => {
      setForm(EMPTY_FORM)
      return Promise.all([queryClient.invalidateQueries({ queryKey: queryKeys.tax.gstStatus }), queryClient.invalidateQueries({ queryKey: queryKeys.tax.gstRegistrations })])
    }
  })

  function updateField<Field extends keyof RegistrationForm>(field: Field, value: RegistrationForm[Field]) {
    setForm((current) => ({ ...current, [field]: value }))
  }

  function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    // A deregistration carries no configuration at all: the number, basis and
    // frequency that applied until that date stay readable on the row before
    // it, and repeating them would make it ambiguous whether they still held.
    const payload: Partial<GstRegistrationCreate> = form.registered
      ? {
          registered: true,
          effective_from: form.effective_from,
          gst_number: form.gst_number,
          basis: form.basis,
          filing_frequency: form.filing_frequency,
          period_anchor_month: Number(form.period_anchor_month),
          taxable_activity_start: form.taxable_activity_start || null,
          reason: form.reason
        }
      : { registered: false, effective_from: form.effective_from, reason: form.reason }
    mutation.mutate(payload)
  }

  const fieldErrors = errorsByField(mutation.error)
  const current = status.data

  return (
    <section className="mt-5">
      <h2 className="h4">GST registration</h2>
      <p>
        A registration is a history, not a setting. Each change is recorded from a date and never edited, so a return already filed keeps reading the way it was filed. Correcting a
        record means superseding it, which leaves both the mistake and the correction visible.
      </p>

      {status.isPending && <Spinner animation="border" size="sm" role="status" aria-label="Loading GST status" />}
      {current && (
        <Alert variant={current.registered ? 'success' : 'secondary'}>
          {current.registered ? (
            <>
              <strong>Registered</strong> as at {formatDate(current.as_at)} — {describe(current.registration as GstRegistration)}, GST number {current.registration?.gst_number}.
              {current.taxable_period && (
                <>
                  {' '}
                  The current taxable period runs {formatDate(current.taxable_period.start)} to {formatDate(current.taxable_period.end)}
                  {current.taxable_period.clipped && ' (a short period, because an arrangement started or changed inside the cycle)'}.
                </>
              )}
            </>
          ) : (
            <>
              <strong>Not registered</strong> as at {formatDate(current.as_at)}.{' '}
              {current.has_history ? 'A registration was recorded and later ceased.' : 'No registration has been recorded, so no GST return period exists.'}
            </>
          )}
        </Alert>
      )}

      <h3 className="h5 mt-4">Record a change</h3>
      <Form onSubmit={submit}>
        {'non_field_errors' in fieldErrors && <Alert variant="danger">{fieldErrors.non_field_errors}</Alert>}
        <Form.Group className="mb-3" controlId="gst-registered">
          <Form.Check
            type="switch"
            label="Registered for GST from this date"
            checked={form.registered}
            onChange={(event) => updateField('registered', event.target.checked)}
            aria-describedby="gst-registered-help"
          />
          <Form.Text id="gst-registered-help">Turn this off to record a deregistration or cessation. Supplies made after that date fall in no return period.</Form.Text>
        </Form.Group>
        <Form.Group className="mb-3" controlId="gst-effective-from">
          <Form.Label>Effective from</Form.Label>
          <Form.Control
            required
            type="date"
            value={form.effective_from}
            onChange={(event) => updateField('effective_from', event.target.value)}
            isInvalid={'effective_from' in fieldErrors}
            aria-describedby="gst-effective-from-help"
          />
          <Form.Control.Feedback type="invalid">{fieldErrors.effective_from}</Form.Control.Feedback>
          <Form.Text id="gst-effective-from-help">Must be later than the most recent recorded change. A first registration may be backdated freely.</Form.Text>
        </Form.Group>
        {form.registered && (
          <>
            <Form.Group className="mb-3" controlId="gst-number">
              <Form.Label>GST number</Form.Label>
              <Form.Control
                required
                maxLength={11}
                value={form.gst_number}
                onChange={(event) => updateField('gst_number', event.target.value)}
                isInvalid={'gst_number' in fieldErrors}
                aria-describedby="gst-number-help"
              />
              <Form.Control.Feedback type="invalid">{fieldErrors.gst_number}</Form.Control.Feedback>
              <Form.Text id="gst-number-help">Eight or nine digits. Checked against Inland Revenue&apos;s check digit, so a transposed pair is caught here.</Form.Text>
            </Form.Group>
            <Form.Group className="mb-3" controlId="gst-basis">
              <Form.Label>Accounting basis</Form.Label>
              <Form.Select
                value={form.basis}
                onChange={(event) => updateField('basis', event.target.value as GstBasis)}
                isInvalid={'basis' in fieldErrors}
                aria-describedby="gst-basis-help"
              >
                <option value="payments">Payments</option>
                <option value="invoice">Invoice</option>
                <option value="hybrid">Hybrid</option>
              </Form.Select>
              <Form.Control.Feedback type="invalid">{fieldErrors.basis}</Form.Control.Feedback>
              <Form.Text id="gst-basis-help">
                Payments accounts on money received. Invoice accounts on the earlier of an invoice issued and a payment received. Hybrid combines the two.
              </Form.Text>
            </Form.Group>
            <Form.Group className="mb-3" controlId="gst-frequency">
              <Form.Label>Filing frequency</Form.Label>
              <Form.Select
                value={form.filing_frequency}
                onChange={(event) => updateField('filing_frequency', event.target.value as GstFrequency)}
                isInvalid={'filing_frequency' in fieldErrors}
              >
                <option value="monthly">Monthly</option>
                <option value="two_monthly">Two-monthly</option>
                <option value="six_monthly">Six-monthly</option>
              </Form.Select>
              <Form.Control.Feedback type="invalid">{fieldErrors.filing_frequency}</Form.Control.Feedback>
            </Form.Group>
            {form.filing_frequency !== 'monthly' && (
              <Form.Group className="mb-3" controlId="gst-anchor">
                <Form.Label>Taxable periods end in</Form.Label>
                <Form.Select
                  value={form.period_anchor_month}
                  onChange={(event) => updateField('period_anchor_month', event.target.value)}
                  isInvalid={'period_anchor_month' in fieldErrors}
                  aria-describedby="gst-anchor-help"
                >
                  {MONTH_NAMES.map((name, index) => (
                    <option key={name} value={String(index + 1)}>
                      {name}
                    </option>
                  ))}
                </Form.Select>
                <Form.Control.Feedback type="invalid">{fieldErrors.period_anchor_month}</Form.Control.Feedback>
                <Form.Text id="gst-anchor-help">
                  Pick any month a period ends in; the rest of the cycle follows from it. Two-monthly filing ends in either the odd or the even months, and six-monthly in
                  March/September, April/October, or May/November.
                </Form.Text>
              </Form.Group>
            )}
            <Form.Group className="mb-3" controlId="gst-activity-start">
              <Form.Label>Taxable activity started</Form.Label>
              <Form.Control
                type="date"
                value={form.taxable_activity_start}
                onChange={(event) => updateField('taxable_activity_start', event.target.value)}
                isInvalid={'taxable_activity_start' in fieldErrors}
                aria-describedby="gst-activity-start-help"
              />
              <Form.Control.Feedback type="invalid">{fieldErrors.taxable_activity_start}</Form.Control.Feedback>
              <Form.Text id="gst-activity-start-help">
                Optional. The activity may have begun well before registration; this is what the turnover threshold is measured from.
              </Form.Text>
            </Form.Group>
          </>
        )}
        <Form.Group className="mb-3" controlId="gst-reason">
          <Form.Label>Reason</Form.Label>
          <Form.Control as="textarea" rows={2} value={form.reason} onChange={(event) => updateField('reason', event.target.value)} />
        </Form.Group>
        <Button type="submit" disabled={mutation.isPending}>
          {mutation.isPending ? 'Recording…' : 'Record change'}
        </Button>
        {mutation.isSuccess && <span className="ms-3 text-success">Change recorded.</span>}
      </Form>

      <h3 className="h5 mt-4">Recorded history</h3>
      {history.data && history.data.length === 0 && <p>No GST arrangement has been recorded yet.</p>}
      {history.data && history.data.length > 0 && (
        <Table responsive size="sm">
          <thead>
            <tr>
              <th>From</th>
              <th>Status</th>
              <th>GST number</th>
              <th>Periods end in</th>
              <th>Reason</th>
            </tr>
          </thead>
          <tbody>
            {history.data.map((registration) => (
              <tr key={registration.pk} className={registration.superseded ? 'text-body-secondary' : undefined}>
                <td>{formatDate(registration.effective_from)}</td>
                <td>
                  {describe(registration)}{' '}
                  {registration.superseded && (
                    <Badge bg="secondary" title="Corrected by a later record. Kept because a return filed before the correction was filed under it.">
                      superseded
                    </Badge>
                  )}
                </td>
                <td>{registration.gst_number || '—'}</td>
                <td>{registration.period_anchor_month ? MONTH_NAMES[registration.period_anchor_month - 1] : '—'}</td>
                <td>{registration.reason || '—'}</td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </section>
  )
}

export { GstRegistrationSettings }
