import React from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Alert, Badge, Button, Form, Table } from 'react-bootstrap'

import { reverseInputApplication } from '../api/applications'
import { formatDateTime, formatMeasure, formatQuantity } from '../utils'
import { APPLICATION_STATUS_LABELS, InputApplication, InputApplicationLine, TARGET_TYPE_LABELS } from '../types/applications'
import { queryKeys } from '../query'

const STATUS_VARIANTS: Record<string, string> = {
  draft: 'secondary',
  posted: 'success',
  reversed: 'warning'
}

function invalidateApplications(queryClient: ReturnType<typeof useQueryClient>) {
  return Promise.all([queryClient.invalidateQueries({ queryKey: queryKeys.applications.all }), queryClient.invalidateQueries({ queryKey: queryKeys.inventory.all })])
}

function ReverseApplicationButton({ application }: { application: InputApplication }) {
  const queryClient = useQueryClient()
  const [reason, setReason] = React.useState('')
  const [open, setOpen] = React.useState(false)
  const [error, setError] = React.useState<string>()
  const mutation = useMutation({
    mutationFn: () => reverseInputApplication(application.pk, { reason }),
    onSuccess: () => {
      setOpen(false)
      setReason('')
      setError(undefined)
      return invalidateApplications(queryClient)
    },
    onError: (caught: unknown) => setError(caught instanceof Error ? caught.message : String(caught))
  })

  if (application.status !== 'posted') {
    return <span className="text-muted">—</span>
  }
  if (!open) {
    return (
      <Button size="sm" variant="outline-danger" onClick={() => setOpen(true)}>
        Reverse
      </Button>
    )
  }
  return (
    <div className="d-flex flex-column gap-1">
      <Form.Control size="sm" value={reason} placeholder="Why was this wrong?" onChange={(event) => setReason(event.target.value)} />
      <div className="d-flex gap-1">
        <Button size="sm" variant="danger" disabled={!reason.trim() || mutation.isPending} onClick={() => mutation.mutate()}>
          {mutation.isPending ? 'Reversing…' : 'Confirm'}
        </Button>
        <Button size="sm" variant="outline-secondary" onClick={() => setOpen(false)}>
          Cancel
        </Button>
      </div>
      {error && (
        <Alert className="mb-0 py-1 px-2" variant="danger">
          {error}
        </Alert>
      )}
    </div>
  )
}

// The suggestion and the confirmed amount are always shown together. Seeing one
// without the other is what makes an override invisible, which is the whole
// thing the override reason exists to prevent.
function LineAmounts({ line }: { line: InputApplicationLine }) {
  return (
    <>
      <div>{formatMeasure(line.applied_base_quantity, line.base_unit)}</div>
      {line.calculated_base_quantity !== null && line.calculated_base_quantity !== line.applied_base_quantity && (
        <div className="text-muted small">calculated {formatQuantity(line.calculated_base_quantity)}</div>
      )}
      {line.waste_base_quantity !== '0.000000000' && <div className="text-muted small">waste {formatMeasure(line.waste_base_quantity, line.base_unit)}</div>}
    </>
  )
}

function TargetSummary({ line }: { line: InputApplicationLine }) {
  if (line.targets.length === 0) {
    return <span className="text-muted">No targets recorded</span>
  }
  const first = line.targets[0]
  const label = TARGET_TYPE_LABELS[first.target_type]
  if (line.targets.length === 1) {
    return <>{first.label || label}</>
  }
  return (
    <>
      {line.targets.length} × {label.toLowerCase()}
    </>
  )
}

function ApplicationLinesTable({ lines }: { lines: Array<InputApplicationLine> }) {
  return (
    <Table size="sm" className="mb-0">
      <thead>
        <tr>
          <th>Applied</th>
          <th>Targets</th>
          <th>Working</th>
          <th>Movements</th>
        </tr>
      </thead>
      <tbody>
        {lines.map((line) => (
          <tr key={line.pk}>
            <td>
              <LineAmounts line={line} />
              {line.override_reason && <div className="text-muted small">{line.override_reason}</div>}
            </td>
            <td>
              <TargetSummary line={line} />
            </td>
            <td className="small text-muted">
              {line.formula_basis_quantity !== null ? `${formatQuantity(line.formula_basis_quantity)} ${line.formula_basis_unit}` : line.usage_basis.replace('_', ' ')}
            </td>
            <td className="small">
              {line.consumption_movement !== null ? `#${line.consumption_movement}` : '—'}
              {line.waste_movement !== null && <span className="text-muted"> · waste #{line.waste_movement}</span>}
            </td>
          </tr>
        ))}
      </tbody>
    </Table>
  )
}

function ApplicationTable({ applications }: { applications: Array<InputApplication> }) {
  if (applications.length === 0) {
    return <p className="text-muted mb-0">No input applications recorded yet.</p>
  }
  return (
    <Table>
      <thead>
        <tr>
          <th>Applied</th>
          <th>Status</th>
          <th>Went on</th>
          <th>Lines</th>
          <th>Correct</th>
        </tr>
      </thead>
      <tbody>
        {applications.map((application) => (
          <tr key={application.pk}>
            <td>{formatDateTime(application.applied_at)}</td>
            <td>
              <Badge bg={STATUS_VARIANTS[application.status]}>{APPLICATION_STATUS_LABELS[application.status]}</Badge>
              {application.status === 'reversed' && application.reverse_reason && <div className="text-muted small">{application.reverse_reason}</div>}
            </td>
            <td>{application.target_summary || <span className="text-muted">Nothing recorded</span>}</td>
            <td>
              <ApplicationLinesTable lines={application.lines} />
            </td>
            <td>
              <ReverseApplicationButton application={application} />
            </td>
          </tr>
        ))}
      </tbody>
    </Table>
  )
}

export { ApplicationLinesTable, ApplicationTable, ReverseApplicationButton, invalidateApplications }
