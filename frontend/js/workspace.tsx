import React from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Form } from 'react-bootstrap'

import { updateWorkspace } from './api/workspace'
import { queryKeys } from './query'
import { Workspace, WorkspaceMode, WorkspaceUpdate } from './types/workspace'

interface WorkspaceSettingsProps {
  workspace: Workspace
}

interface WorkspaceModeRouteProps {
  workspace: Workspace
  enabledModes: ReadonlyArray<WorkspaceMode>
  children: React.ReactNode
}

function WorkspaceModeRoute({ workspace, enabledModes, children }: WorkspaceModeRouteProps) {
  if (!enabledModes.includes(workspace.mode)) {
    const profile = workspace.mode === 'garden' ? 'Garden' : 'Nursery'
    return <Alert variant="warning">This feature is not enabled for the {profile} profile.</Alert>
  }
  return children
}

function WorkspaceSettings({ workspace }: WorkspaceSettingsProps) {
  const queryClient = useQueryClient()
  const [form, setForm] = React.useState<WorkspaceUpdate>({
    name: workspace.name,
    mode: workspace.mode,
    currency_code: workspace.currency_code,
    default_tax_rate: workspace.default_tax_rate,
    timezone: workspace.timezone,
    measurement_system: workspace.measurement_system,
    override_tolerance_percent: workspace.override_tolerance_percent,
    override_tolerance_floor: workspace.override_tolerance_floor,
    stocktake_two_person_required: workspace.stocktake_two_person_required
  })
  const mutation = useMutation({
    mutationFn: updateWorkspace,
    onSuccess: (updated) => queryClient.setQueryData(queryKeys.workspace.current, updated)
  })

  React.useEffect(() => {
    setForm({
      name: workspace.name,
      mode: workspace.mode,
      currency_code: workspace.currency_code,
      default_tax_rate: workspace.default_tax_rate,
      timezone: workspace.timezone,
      measurement_system: workspace.measurement_system,
      override_tolerance_percent: workspace.override_tolerance_percent,
      override_tolerance_floor: workspace.override_tolerance_floor,
      stocktake_two_person_required: workspace.stocktake_two_person_required
    })
  }, [workspace])

  function updateField<Field extends keyof WorkspaceUpdate>(field: Field, value: WorkspaceUpdate[Field]) {
    setForm((current) => ({ ...current, [field]: value }))
  }

  function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    mutation.mutate({ ...form, currency_code: form.currency_code.toUpperCase() })
  }

  return (
    <main className="container py-3">
      <h1>Workspace settings</h1>
      <p>Profile changes alter presentation and defaults without deleting cultivation data.</p>
      <Form onSubmit={submit}>
        <Form.Group className="mb-3" controlId="workspace-name">
          <Form.Label>Workspace name</Form.Label>
          <Form.Control required maxLength={255} value={form.name} onChange={(event) => updateField('name', event.target.value)} />
        </Form.Group>
        <Form.Group className="mb-3" controlId="workspace-mode">
          <Form.Label>Profile</Form.Label>
          <Form.Select value={form.mode} onChange={(event) => updateField('mode', event.target.value as WorkspaceMode)}>
            <option value="garden">Garden</option>
            <option value="nursery">Nursery</option>
          </Form.Select>
        </Form.Group>
        <Form.Group className="mb-3" controlId="workspace-currency">
          <Form.Label>Currency code</Form.Label>
          <Form.Control
            required
            minLength={3}
            maxLength={3}
            pattern="[A-Za-z]{3}"
            value={form.currency_code}
            onChange={(event) => updateField('currency_code', event.target.value)}
            aria-describedby="workspace-currency-help"
          />
          <Form.Text id="workspace-currency-help">Three-letter ISO 4217 code, such as USD or NZD.</Form.Text>
        </Form.Group>
        <Form.Group className="mb-3" controlId="workspace-tax-rate">
          <Form.Label>Default tax rate (%)</Form.Label>
          <Form.Control
            required
            type="number"
            min="0"
            max="100"
            step="0.0001"
            value={form.default_tax_rate}
            onChange={(event) => updateField('default_tax_rate', event.target.value)}
          />
        </Form.Group>
        <Form.Group className="mb-3" controlId="workspace-timezone">
          <Form.Label>Timezone</Form.Label>
          <Form.Control
            required
            maxLength={64}
            value={form.timezone}
            onChange={(event) => updateField('timezone', event.target.value)}
            aria-describedby="workspace-timezone-help"
          />
          <Form.Text id="workspace-timezone-help">IANA name, such as UTC or Pacific/Auckland.</Form.Text>
        </Form.Group>
        <Form.Group className="mb-3" controlId="workspace-measurement">
          <Form.Label>Display measurements</Form.Label>
          <Form.Select value={form.measurement_system} onChange={(event) => updateField('measurement_system', event.target.value as WorkspaceUpdate['measurement_system'])}>
            <option value="metric">Metric</option>
            <option value="imperial">Imperial</option>
          </Form.Select>
        </Form.Group>
        <Form.Group className="mb-3" controlId="workspace-override-percent">
          <Form.Label>Input override tolerance (%)</Form.Label>
          <Form.Control
            type="number"
            min="0"
            max="100"
            step="0.0001"
            value={form.override_tolerance_percent}
            onChange={(event) => updateField('override_tolerance_percent', event.target.value)}
            aria-describedby="workspace-override-percent-help"
          />
          <Form.Text id="workspace-override-percent-help">How far a confirmed input quantity may differ from the calculated suggestion before a reason is required.</Form.Text>
        </Form.Group>
        <Form.Group className="mb-3" controlId="workspace-override-floor">
          <Form.Label>Input override floor</Form.Label>
          <Form.Control
            type="number"
            min="0"
            step="0.000000001"
            value={form.override_tolerance_floor}
            onChange={(event) => updateField('override_tolerance_floor', event.target.value)}
            aria-describedby="workspace-override-floor-help"
          />
          <Form.Text id="workspace-override-floor-help">
            Smallest difference, in an item&apos;s own unit, that can require a reason. Zero disables it, so the percentage alone applies.
          </Form.Text>
        </Form.Group>
        {form.mode === 'nursery' && (
          <Form.Check
            className="mb-3"
            type="switch"
            id="stocktake-two-person"
            label="Require a different reviewer from every stocktake counter"
            checked={form.stocktake_two_person_required}
            onChange={(event) => updateField('stocktake_two_person_required', event.target.checked)}
          />
        )}
        <Button type="submit" disabled={mutation.isPending}>
          {mutation.isPending ? 'Saving…' : 'Save settings'}
        </Button>
        {mutation.isSuccess && <span className="ms-3 text-success">Settings saved.</span>}
      </Form>
    </main>
  )
}

export { WorkspaceModeRoute, WorkspaceSettings }
