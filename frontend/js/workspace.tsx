import React from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Form } from 'react-bootstrap'

import { restoreAttachmentArchive } from './api/attachments'
import { updateWorkspace } from './api/workspace'
import { GstRegistrationSettings } from './tax/registration.js'
import { queryKeys } from './query'
import { GardenExperience, Workspace, WorkspaceMode, WorkspaceUpdate } from './types/workspace'
import { AttachmentArchiveReport } from './types/attachments'

// The guided setup owns garden_setup_state; this screen edits everything else,
// and must not send that field back and undo a gardener's answer.
type WorkspaceProfile = Omit<WorkspaceUpdate, 'garden_setup_state'>

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
  const [form, setForm] = React.useState<WorkspaceProfile>({
    name: workspace.name,
    legal_name: workspace.legal_name,
    trading_name: workspace.trading_name,
    business_address: workspace.business_address,
    mode: workspace.mode,
    garden_experience: workspace.garden_experience,
    currency_code: workspace.currency_code,
    default_tax_rate: workspace.default_tax_rate,
    sales_prices_include_tax: workspace.sales_prices_include_tax,
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
  const [archive, setArchive] = React.useState<File | null>(null)
  const [archiveReport, setArchiveReport] = React.useState<AttachmentArchiveReport | null>(null)
  const restoreMutation = useMutation({
    mutationFn: ({ file, dryRun }: { file: File; dryRun: boolean }) => restoreAttachmentArchive(file, dryRun),
    onSuccess: setArchiveReport
  })

  React.useEffect(() => {
    setForm({
      name: workspace.name,
      legal_name: workspace.legal_name,
      trading_name: workspace.trading_name,
      business_address: workspace.business_address,
      mode: workspace.mode,
      garden_experience: workspace.garden_experience,
      currency_code: workspace.currency_code,
      default_tax_rate: workspace.default_tax_rate,
      sales_prices_include_tax: workspace.sales_prices_include_tax,
      timezone: workspace.timezone,
      measurement_system: workspace.measurement_system,
      override_tolerance_percent: workspace.override_tolerance_percent,
      override_tolerance_floor: workspace.override_tolerance_floor,
      stocktake_two_person_required: workspace.stocktake_two_person_required
    })
  }, [workspace])

  function updateField<Field extends keyof WorkspaceProfile>(field: Field, value: WorkspaceProfile[Field]) {
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
        {form.mode === 'nursery' && (
          <fieldset className="mb-3">
            <legend className="h5">Seller identity</legend>
            <p className="text-muted">
              Printed on every taxable supply and correction document. The GST number is not entered here: it comes from the registration in force on the document&apos;s own date,
              recorded further down this page.
            </p>
            <Form.Group className="mb-3" controlId="workspace-legal-name">
              <Form.Label>Legal name</Form.Label>
              <Form.Control
                maxLength={255}
                value={form.legal_name}
                onChange={(event) => updateField('legal_name', event.target.value)}
                aria-describedby="workspace-legal-name-help"
              />
              <Form.Text id="workspace-legal-name-help">
                The registered name of the entity making supplies. A document cannot be issued without it, and the workspace name is not substituted for it — the two are often
                different, and the wrong name on a tax invoice is a defective document.
              </Form.Text>
            </Form.Group>
            <Form.Group className="mb-3" controlId="workspace-trading-name">
              <Form.Label>Trading name</Form.Label>
              <Form.Control
                maxLength={255}
                value={form.trading_name}
                onChange={(event) => updateField('trading_name', event.target.value)}
                aria-describedby="workspace-trading-name-help"
              />
              <Form.Text id="workspace-trading-name-help">Optional, and shown alongside the legal name rather than instead of it.</Form.Text>
            </Form.Group>
            <Form.Group className="mb-3" controlId="workspace-business-address">
              <Form.Label>Business address</Form.Label>
              <Form.Control as="textarea" rows={3} value={form.business_address} onChange={(event) => updateField('business_address', event.target.value)} />
            </Form.Group>
          </fieldset>
        )}
        <Form.Group className="mb-3" controlId="workspace-mode">
          <Form.Label>Profile</Form.Label>
          <Form.Select value={form.mode} onChange={(event) => updateField('mode', event.target.value as WorkspaceMode)}>
            <option value="garden">Garden</option>
            <option value="nursery">Nursery</option>
          </Form.Select>
        </Form.Group>
        {form.mode === 'garden' && (
          <Form.Group className="mb-3" controlId="workspace-garden-experience">
            <Form.Label>Garden experience</Form.Label>
            <Form.Select
              value={form.garden_experience}
              onChange={(event) => updateField('garden_experience', event.target.value as GardenExperience)}
              aria-describedby="workspace-garden-experience-help"
            >
              <option value="basic">Basic</option>
              <option value="advanced">Advanced</option>
            </Form.Select>
            <Form.Text id="workspace-garden-experience-help">
              Basic hides supplier, price, tax, receiving, and costing fields and fills them in for you. Advanced shows every field. Switching changes no existing record.
            </Form.Text>
          </Form.Group>
        )}
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
        <Form.Group className="mb-3" controlId="workspace-sales-tax-mode">
          <Form.Check
            label="Prices entered on new sales orders include tax / GST"
            checked={form.sales_prices_include_tax}
            onChange={(event) => updateField('sales_prices_include_tax', event.target.checked)}
          />
          <Form.Text>Each order snapshots this default and may override it while still a quote or draft.</Form.Text>
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
          <Form.Select value={form.measurement_system} onChange={(event) => updateField('measurement_system', event.target.value as WorkspaceProfile['measurement_system'])}>
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
      <section className="mt-5" aria-labelledby="photo-archive-heading">
        <h2 className="h4" id="photo-archive-heading">
          Photo archive
        </h2>
        <p>Download sanitized full-size photos and their attachment metadata. Restore reconnects photos only when the matching record IDs already exist in this workspace.</p>
        <Button as="a" href="/attachments/archive/" variant="outline-primary">
          Download photo archive
        </Button>
        <Form.Group className="mt-3" controlId="photo-archive-file">
          <Form.Label>Restore a photo archive</Form.Label>
          <Form.Control
            type="file"
            accept="application/zip,.zip"
            onChange={(event: React.ChangeEvent<HTMLInputElement>) => {
              setArchive(event.target.files?.[0] ?? null)
              setArchiveReport(null)
            }}
          />
          <Form.Text>Run the dry check first. It makes no changes.</Form.Text>
        </Form.Group>
        <Button
          className="mt-2"
          variant="outline-secondary"
          disabled={archive === null || restoreMutation.isPending}
          onClick={() => {
            if (archive !== null) restoreMutation.mutate({ file: archive, dryRun: true })
          }}
        >
          {restoreMutation.isPending ? 'Checking…' : 'Check archive'}
        </Button>
        {archiveReport !== null && (
          <Alert className="mt-3" variant={archiveReport.valid ? 'info' : 'danger'}>
            {archiveReport.valid ? (
              <>
                Ready to restore {archiveReport.would_create} photos; {archiveReport.already_present} are already present.
                {archive !== null && archiveReport.would_create > 0 && (
                  <div>
                    <Button
                      className="mt-2"
                      variant="warning"
                      disabled={restoreMutation.isPending}
                      onClick={() => {
                        if (window.confirm('Restore the checked photos into this workspace?')) {
                          restoreMutation.mutate({ file: archive, dryRun: false })
                        }
                      }}
                    >
                      Restore checked photos
                    </Button>
                  </div>
                )}
                {archiveReport.created !== undefined && <div className="mt-2 text-success">Restored {archiveReport.created} photos.</div>}
              </>
            ) : (
              <ul className="mb-0">
                {archiveReport.errors.map((error) => (
                  <li key={error}>{error}</li>
                ))}
              </ul>
            )}
          </Alert>
        )}
      </section>
      {workspace.mode === 'nursery' && <GstRegistrationSettings />}
    </main>
  )
}

export { WorkspaceModeRoute, WorkspaceSettings }
