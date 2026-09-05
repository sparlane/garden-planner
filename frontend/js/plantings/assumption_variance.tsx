import React from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Badge, Button, Col, Form, Row, Table } from 'react-bootstrap'

import { getAssumptionRevisionDraft, reviseAssumption } from '../api/plantings'
import { queryKeys } from '../query'
import { NurseryAssumptionRevisionDraft, NurseryAssumptionVariance, NurseryPlanningAssumption } from '../types/plantings'
import { errorsByField, formatQuantity } from '../utils'

// A planning figure and what happened under it are two numbers that mean
// nothing apart, so they are always rendered together, with the count behind
// the observation. Three batches is not evidence, and a variance shown without
// its sample invites exactly the overreaction the report exists to prevent.
function VarianceFigure({
  label,
  assumed,
  observed,
  variance,
  sample,
  sampleNoun,
  diverged
}: {
  label: string
  assumed: React.ReactNode
  observed: string | null
  variance: string | null
  sample: number
  sampleNoun: string
  diverged: boolean
}) {
  return (
    <Col md={6} className="mb-2">
      <div className="text-muted small">{label}</div>
      <div>
        Assumed <strong>{assumed}</strong>
        {observed === null ? (
          <span className="text-muted"> · nothing observed yet</span>
        ) : (
          <>
            {' '}
            · observed <strong>{formatQuantity(observed)}</strong>
            {variance !== null && (
              <span className="text-muted">
                {' '}
                ({Number(variance) > 0 ? '+' : ''}
                {formatQuantity(variance)})
              </span>
            )}
          </>
        )}
      </div>
      <div className="small text-muted">
        {sample} {sample === 1 ? sampleNoun : `${sampleNoun}s`} behind it
        {diverged && (
          <>
            {' '}
            <Badge bg="warning" text="dark">
              Beyond tolerance
            </Badge>
          </>
        )}
      </div>
    </Col>
  )
}

// The whole comparison for one assumption version. Shown on the assumption
// itself rather than only in the report, because the decision to keep a figure
// is made here, at the moment of editing it.
function AssumptionVariancePanel({ variance }: { variance: NurseryAssumptionVariance | undefined }) {
  if (!variance) return null
  if (variance.batches === 0) {
    return (
      <Alert variant="secondary" className="mt-2 mb-0 py-2 small">
        Nothing has been sown under this version yet, so there is nothing to compare it with.
      </Alert>
    )
  }
  return (
    <div className="mt-2">
      <Row>
        <VarianceFigure
          label="Germination rate"
          assumed={formatQuantity(variance.assumed_germination_rate)}
          observed={variance.observed_germination_rate}
          variance={variance.germination_variance}
          sample={variance.germination_sowings}
          sampleNoun="closed sowing"
          diverged={variance.germination_diverged}
        />
        <VarianceFigure
          label="Clusters per tray"
          assumed={variance.assumed_tray_density}
          observed={variance.observed_tray_density}
          variance={variance.tray_density_variance}
          sample={variance.tray_fills}
          sampleNoun="tray fill"
          diverged={variance.tray_density_diverged}
        />
      </Row>
      {variance.germination_open_sowings > 0 && (
        <div className="small text-muted">
          {variance.germination_open_sowings} sowing{variance.germination_open_sowings === 1 ? ' is' : 's are'} still germinating and left out of the observed rate, because a count
          that can still rise is a floor rather than a result.
        </div>
      )}
      {variance.tray_fills_shared > 0 && (
        <div className="small text-muted">
          {variance.tray_fills_shared} tray fill{variance.tray_fills_shared === 1 ? ' was' : 's were'} shared with another variety and left out of the density.
        </div>
      )}
      {!variance.sample_sufficient && (
        <div className="small text-muted">
          {variance.batches} batch{variance.batches === 1 ? '' : 'es'} is under the {variance.minimum_samples} this workspace requires before raising a flag, so the gap is reported
          but not raised.
        </div>
      )}
      {variance.stages.length > 0 && (
        <Table size="sm" className="mt-2 mb-0">
          <thead>
            <tr>
              <th>Stage</th>
              <th>Assumed days</th>
              <th>Observed days</th>
              <th>Assumed loss</th>
              <th>Observed loss</th>
            </tr>
          </thead>
          <tbody>
            {variance.stages.map((stage) => (
              <tr key={stage.stage_id}>
                <td>{stage.stage_name}</td>
                <td>{stage.assumed_lead_days}</td>
                <td>
                  {stage.observed_lead_days === null ? <span className="text-muted">—</span> : formatQuantity(stage.observed_lead_days)}
                  <span className="text-muted small"> · {stage.lead_days_samples} closed</span>
                  {stage.lead_days_diverged && (
                    <Badge bg="warning" text="dark" className="ms-1">
                      Beyond tolerance
                    </Badge>
                  )}
                </td>
                <td>{formatQuantity(stage.assumed_loss_rate)}</td>
                <td>
                  {stage.observed_loss_rate === null ? <span className="text-muted">—</span> : formatQuantity(stage.observed_loss_rate)}
                  <span className="text-muted small">
                    {' '}
                    · {stage.lost_units} of {stage.entered_units}
                  </span>
                  {stage.loss_rate_diverged && (
                    <Badge bg="warning" text="dark" className="ms-1">
                      Beyond tolerance
                    </Badge>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
      {variance.unstaged_losses > 0 && (
        <div className="small text-muted mt-1">
          {variance.unstaged_losses} unit{variance.unstaged_losses === 1 ? '' : 's'} were lost with no stage observation standing, so they are counted apart from the stage loss
          rates.
        </div>
      )}
    </div>
  )
}

function DraftField({
  label,
  value,
  source,
  onChange,
  ...control
}: {
  label: string
  value: string
  source: 'observed' | 'assumed'
  onChange: (value: string) => void
} & Omit<React.ComponentProps<typeof Form.Control>, 'value' | 'onChange'>) {
  return (
    <Col md={3}>
      <Form.Label>
        {label} <span className="text-muted small">{source === 'observed' ? 'from what happened' : 'kept as assumed'}</span>
      </Form.Label>
      <Form.Control {...control} value={value} onChange={(event) => onChange(event.target.value)} />
    </Col>
  )
}

// The revision itself. The form arrives pre-filled with the observed figures,
// and nothing is written until an operator submits it: an assumption is a
// judgement about next season, not a mean of the last one, so the system's
// job is to put the gap in front of somebody rather than to close it.
function ReviseAssumptionForm({ assumption, onDone }: { assumption: NurseryPlanningAssumption; onDone: () => void }) {
  const cache = useQueryClient()
  const draft = useQuery({
    queryKey: queryKeys.plantings.assumptionRevisionDraft(assumption.pk),
    queryFn: ({ signal }) => getAssumptionRevisionDraft(assumption.pk, signal)
  })
  const [values, setValues] = React.useState<NurseryAssumptionRevisionDraft | null>(null)
  React.useEffect(() => {
    if (draft.data) setValues(draft.data)
  }, [draft.data])
  const save = useMutation({
    mutationFn: () =>
      reviseAssumption(assumption.pk, {
        effective_from: values?.effective_from,
        germination_rate: values?.germination_rate,
        seeds_per_cluster: values?.seeds_per_cluster,
        tray_density: values?.tray_density,
        stages: (values?.stages ?? []).map((stage) => ({ stage: stage.stage, lead_days: stage.lead_days, loss_rate: stage.loss_rate }))
      }),
    onSuccess: () => {
      cache.invalidateQueries({ queryKey: queryKeys.plantings.planningAssumptions })
      cache.invalidateQueries({ queryKey: queryKeys.plantings.assumptionVariance })
      onDone()
    }
  })
  if (draft.isPending || !values) return <div className="small text-muted mt-2">Reading what happened…</div>
  const fieldErrors = save.error ? errorsByField(save.error) : {}
  function updateStage(stagePk: number, field: 'lead_days' | 'loss_rate', value: string) {
    setValues((current) =>
      current === null
        ? current
        : {
            ...current,
            stages: current.stages.map((stage) => (stage.stage === stagePk ? { ...stage, [field]: field === 'lead_days' ? Number(value) : value } : stage))
          }
    )
  }
  return (
    <Form
      className="border-top pt-2 mt-2"
      onSubmit={(event) => {
        event.preventDefault()
        save.mutate()
      }}
    >
      {values.germination_rate_capped && (
        <Alert variant="info" className="py-2 small">
          More seedlings came up than clusters were sown, which a multigerm crop does. The rate is a fraction the field caps at one, so the draft offers one rather than a version
          that cannot be saved.
        </Alert>
      )}
      <Row className="g-2 align-items-end">
        <DraftField
          label="Effective from"
          type="date"
          required
          source="assumed"
          value={values.effective_from}
          onChange={(value) => setValues({ ...values, effective_from: value })}
        />
        <DraftField
          label="Germination rate"
          type="number"
          min="0.000001"
          max="1"
          step="0.000001"
          required
          source={values.germination_rate_source}
          value={values.germination_rate}
          onChange={(value) => setValues({ ...values, germination_rate: value })}
        />
        <DraftField
          label="Clusters / tray"
          type="number"
          min="1"
          required
          source={values.tray_density_source}
          value={String(values.tray_density)}
          onChange={(value) => setValues({ ...values, tray_density: Number(value) })}
        />
      </Row>
      {values.stages.map((stage) => (
        <Row className="g-2 align-items-end mt-1" key={stage.stage}>
          <Col md={3}>
            <div className="text-muted small">{stage.stage_name}</div>
          </Col>
          <DraftField
            label="Lead days"
            type="number"
            min="0"
            required
            source={stage.lead_days_source}
            value={String(stage.lead_days)}
            onChange={(value) => updateStage(stage.stage, 'lead_days', value)}
          />
          <DraftField
            label="Loss rate"
            type="number"
            min="0"
            max="0.999999"
            step="0.000001"
            required
            source={stage.loss_rate_source}
            value={stage.loss_rate}
            onChange={(value) => updateStage(stage.stage, 'loss_rate', value)}
          />
        </Row>
      ))}
      {'effective_from' in fieldErrors && (
        <Alert variant="danger" className="py-2 small mt-2">
          {fieldErrors.effective_from}
        </Alert>
      )}
      <div className="mt-2 d-flex gap-2">
        <Button type="submit" size="sm" disabled={save.isPending}>
          Save this revision
        </Button>
        <Button type="button" size="sm" variant="outline-secondary" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </Form>
  )
}

export { AssumptionVariancePanel, ReviseAssumptionForm }
