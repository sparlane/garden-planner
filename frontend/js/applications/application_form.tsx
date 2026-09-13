import React from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Card, Col, Form, Row } from 'react-bootstrap'

import { addInputApplication, deleteInputApplication, postInputApplication, previewInputApplication, updateInputApplication } from '../api/applications'
import { getInventoryBalances, getInventoryItems } from '../api/inventory'
import { queryKeys } from '../query'
import { formatMeasure, formatQuantity, localDatetimeInputValue, parseLocalDatetimeInput } from '../utils'
import { ApplicationLineInput, ApplicationPreview, ApplicationTargetType, InputApplication } from '../types/applications'
import { invalidateApplications } from './application_list'

interface ApplicationTargetOption {
  // `${target_type}:${pk}` — stable across re-renders and unique per thing.
  key: string
  target_type: ApplicationTargetType
  pk: number
  label: string
  // Set when this thing cannot be applied to yet, such as a garden area whose
  // physical length unit nobody has confirmed. The option stays visible so the
  // operator can see why rather than wondering where it went.
  blocked?: string
}

interface InputApplicationFormProps {
  targets: Array<ApplicationTargetOption>
  batch?: number | null
  defaultTargetKeys?: Array<string>
  // A whole-tray shortcut. The server expands it into one target per cell, so
  // the stored document still names exactly which cells were filled.
  tray?: number
  title?: string
  onPosted?: (application: InputApplication) => void
}

function targetKey(option: ApplicationTargetOption) {
  return option.key
}

// The catalogue is fetched here rather than pushed in as props, because every
// screen embedding this form would otherwise duplicate the same two queries.
// Targets stay props: they are the one thing each caller genuinely differs on.
function InputApplicationForm({ targets, batch = null, defaultTargetKeys, tray, title = 'Apply an input', onPosted }: InputApplicationFormProps) {
  const queryClient = useQueryClient()
  const [item, setItem] = React.useState<number | ''>('')
  const [stock, setStock] = React.useState('')
  const [quantity, setQuantity] = React.useState('')
  const [waste, setWaste] = React.useState('')
  const [wasteReason, setWasteReason] = React.useState('')
  const [overrideReason, setOverrideReason] = React.useState('')
  const [fillFactor, setFillFactor] = React.useState('')
  const [appliedAt, setAppliedAt] = React.useState(localDatetimeInputValue())
  const [selected, setSelected] = React.useState<Array<string>>(defaultTargetKeys ?? [])
  const [draft, setDraft] = React.useState<InputApplication>()
  const [preview, setPreview] = React.useState<ApplicationPreview>()
  const [error, setError] = React.useState<string>()

  const { data: items = [] } = useQuery({
    queryKey: queryKeys.inventory.items('', '', '', 'active'),
    queryFn: ({ signal }) => getInventoryItems({ active: true }, signal)
  })
  const { data: balances = [] } = useQuery({
    queryKey: queryKeys.inventory.balances(item),
    queryFn: ({ signal }) => getInventoryBalances(Number(item), signal),
    enabled: item !== ''
  })

  const targetsSeedTray = tray !== undefined || targets.some((target) => target.target_type === 'seed_tray_cell')
  const targetsPotFill = targets.some((target) => target.target_type === 'container_fill')
  const chosenTargets = selected.map((key) => targets.find((option) => option.key === key)).filter((option) => option !== undefined)
  // A pot fill owns its media history and its cost, so a line names exactly
  // one fill and records the whole quantity that went into it: there is no
  // allocation basis for a line spread over several, and the server refuses
  // one. A bench filled from the same barrow is therefore one document with a
  // line per pot, which is also what makes it readable afterwards — each pot
  // can say how much mix it holds without dividing anything back out.
  const fillPerLine = chosenTargets.length > 0 && chosenTargets.every((option) => option.target_type === 'container_fill')
  // Seed stock is consumed by a sowing, not by an input application. A tray's
  // cells additionally receive only media and treatments; containers, labels,
  // packaging, and unrelated physical stock have their own workflows.
  const applicableItems = items.filter(
    (entry) =>
      entry.category !== 'seed' &&
      (!targetsPotFill || entry.category === 'growing_media') &&
      (!targetsSeedTray || entry.category === 'growing_media' || entry.category === 'fertilizer_treatment')
  )
  const chosenItem = applicableItems.find((entry) => entry.pk === item)
  const chosenBalance = balances.find((entry) => `${entry.lot}:${entry.location}` === stock)
  const previewLines = preview?.lines ?? []
  const previewLine = previewLines[0]
  // Every line draws on the same lot, and the server subtracts them in order,
  // so what the lot is left with is the last line's figure rather than the
  // first's. A document that showed the first would tell an operator filling
  // forty pots that the lot had barely been touched.
  const finalLine = previewLines[previewLines.length - 1]
  const overrideRequired = previewLines.some((line) => line.override_required)
  const short = previewLines.some((line) => line.short)
  const selectable = targets.filter((option) => !option.blocked)
  const blocked = targets.filter((option) => option.blocked)

  function toggleTarget(key: string) {
    setSelected((current) => (current.includes(key) ? current.filter((entry) => entry !== key) : [...current, key]))
    discardPreview()
  }

  function discardPreview() {
    setPreview(undefined)
  }

  function linePayload(keys: Array<string>, wasted: string): ApplicationLineInput {
    return {
      item: Number(item),
      lot: chosenBalance!.lot,
      ...(targetsPotFill ? { usage_basis: 'manual' as const } : {}),
      applied_quantity: quantity,
      unit_code: chosenItem?.base_unit ?? null,
      fill_factor: targetsPotFill || fillFactor === '' ? null : fillFactor,
      waste_quantity: wasted === '' ? '0' : wasted,
      waste_reason: wasteReason,
      override_reason: overrideReason,
      targets: tray
        ? []
        : keys.map((key) => {
            const option = targets.find((entry) => entry.key === key)
            return { target_type: option!.target_type, target: option!.pk }
          }),
      tray: tray ?? null
    }
  }

  // The mix left in the barrow is one amount for the whole job, not one per
  // pot, so it stays on a single line rather than being counted once for every
  // pot on the bench. It never entered a pot either way — waste is stock
  // discarded, and a fill's contents only ever count what was applied to it.
  function linePayloads(): Array<ApplicationLineInput> {
    if (!fillPerLine) return [linePayload(selected, waste)]
    return selected.map((key, index) => linePayload([key], index === 0 ? waste : '0'))
  }

  const checkMutation = useMutation({
    mutationFn: async () => {
      const applied = parseLocalDatetimeInput(appliedAt)
      if (applied === null) {
        throw new Error('Enter when this input was applied.')
      }
      const payload = {
        applied_at: applied.toISOString(),
        source_location: chosenBalance!.location,
        batch,
        lines: linePayloads()
      }
      const saved = draft ? await updateInputApplication(draft.pk, payload) : await addInputApplication(payload)
      return { saved, state: await previewInputApplication(saved.pk) }
    },
    onSuccess: ({ saved, state }) => {
      setDraft(saved)
      setPreview(state)
      setError(undefined)
    },
    onError: (caught: unknown) => setError(caught instanceof Error ? caught.message : String(caught))
  })

  const postMutation = useMutation({
    mutationFn: () =>
      postInputApplication(draft!.pk, {
        revision: preview!.revision,
        availability_digest: preview!.availability_digest
      }),
    onSuccess: async (application) => {
      await invalidateApplications(queryClient)
      reset()
      onPosted?.(application)
    },
    onError: (caught: unknown) => setError(caught instanceof Error ? caught.message : String(caught))
  })

  const discardMutation = useMutation({
    mutationFn: () => deleteInputApplication(draft!.pk),
    onSuccess: () => {
      reset()
      return invalidateApplications(queryClient)
    }
  })

  function reset() {
    setDraft(undefined)
    setPreview(undefined)
    setQuantity('')
    setWaste('')
    setWasteReason('')
    setOverrideReason('')
    setSelected(defaultTargetKeys ?? [])
    setError(undefined)
  }

  const readyToCheck = item !== '' && chosenBalance !== undefined && quantity.trim() !== '' && (tray !== undefined || selected.length > 0)
  const readyToPost = preview !== undefined && !checkMutation.isPending && (!overrideRequired || overrideReason.trim() !== '')

  return (
    <Card className="mb-4">
      <Card.Body>
        <Card.Title>{title}</Card.Title>
        <Row className="g-2">
          <Col md={4}>
            <Form.Group className="mb-3" controlId="application-item">
              <Form.Label>Item</Form.Label>
              <Form.Select
                value={item}
                onChange={(event) => {
                  setItem(event.target.value === '' ? '' : Number(event.target.value))
                  setStock('')
                  discardPreview()
                }}
              >
                <option value="">Select an item</option>
                {applicableItems.map((entry) => (
                  <option key={entry.pk} value={entry.pk}>
                    {entry.name}
                  </option>
                ))}
              </Form.Select>
            </Form.Group>
          </Col>
          <Col md={4}>
            <Form.Group className="mb-3" controlId="application-lot">
              <Form.Label>Lot and location</Form.Label>
              <Form.Select
                value={stock}
                disabled={item === ''}
                onChange={(event) => {
                  setStock(event.target.value)
                  discardPreview()
                }}
              >
                <option value="">Select stock</option>
                {balances.map((entry) => (
                  <option key={`${entry.lot}-${entry.location}`} value={`${entry.lot}:${entry.location}`}>
                    {entry.lot_identifier} · {entry.location_name} · {formatMeasure(entry.available_quantity, entry.base_unit)}
                  </option>
                ))}
              </Form.Select>
              {item !== '' && balances.length === 0 && <Form.Text className="text-warning">No stock of this item is on hand.</Form.Text>}
            </Form.Group>
          </Col>
          <Col md={4}>
            <Form.Group className="mb-3" controlId="application-applied-at">
              <Form.Label>Applied</Form.Label>
              <Form.Control
                type="datetime-local"
                value={appliedAt}
                onChange={(event) => {
                  setAppliedAt(event.target.value)
                  discardPreview()
                }}
              />
            </Form.Group>
          </Col>
        </Row>

        {tray === undefined && (
          <Form.Group className="mb-3">
            <Form.Label>Applied to</Form.Label>
            <div className="d-flex flex-wrap gap-3">
              {selectable.map((option) => (
                <Form.Check
                  key={targetKey(option)}
                  id={`application-target-${targetKey(option)}`}
                  type="checkbox"
                  label={option.label}
                  checked={selected.includes(option.key)}
                  onChange={() => toggleTarget(option.key)}
                />
              ))}
            </div>
            {selectable.length === 0 && <Form.Text className="text-muted">Nothing here can receive an input yet.</Form.Text>}
            {blocked.map((option) => (
              <Form.Text key={targetKey(option)} className="d-block text-warning">
                {option.label}: {option.blocked}
              </Form.Text>
            ))}
          </Form.Group>
        )}

        <Row className="g-2">
          <Col md={3}>
            <Form.Group className="mb-3" controlId="application-quantity">
              <Form.Label>
                {targetsPotFill ? `Quantity applied to ${fillPerLine && selected.length > 1 ? 'each fill' : 'the whole fill'}` : 'Confirmed quantity'}
                {chosenItem ? ` (${chosenItem.base_unit})` : ''}
              </Form.Label>
              <Form.Control
                value={quantity}
                inputMode="decimal"
                placeholder="e.g. 0.96"
                onChange={(event) => {
                  setQuantity(event.target.value)
                  discardPreview()
                }}
              />
            </Form.Group>
          </Col>
          {!targetsPotFill && (
            <Col md={3}>
              <Form.Group className="mb-3" controlId="application-fill-factor">
                <Form.Label>Fill factor</Form.Label>
                <Form.Control
                  value={fillFactor}
                  inputMode="decimal"
                  placeholder="1.0"
                  onChange={(event) => {
                    setFillFactor(event.target.value)
                    discardPreview()
                  }}
                />
                <Form.Text className="text-muted">How full each cell was packed.</Form.Text>
              </Form.Group>
            </Col>
          )}
          <Col md={3}>
            <Form.Group className="mb-3" controlId="application-waste">
              <Form.Label>Waste{fillPerLine && selected.length > 1 ? ' (for the whole bench)' : ''}</Form.Label>
              <Form.Control
                value={waste}
                inputMode="decimal"
                placeholder="0"
                onChange={(event) => {
                  setWaste(event.target.value)
                  discardPreview()
                }}
              />
            </Form.Group>
          </Col>
          <Col md={3}>
            <Form.Group className="mb-3" controlId="application-waste-reason">
              <Form.Label>Waste reason</Form.Label>
              <Form.Control value={wasteReason} onChange={(event) => setWasteReason(event.target.value)} placeholder="Required if any waste" />
            </Form.Group>
          </Col>
        </Row>

        {previewLine && finalLine && (
          <Alert variant={short ? 'danger' : 'info'}>
            <div>{previewLine.formula}</div>
            <div className="small">
              {previewLine.calculated_base_quantity === null ? 'Manual quantity' : `Calculated ${formatMeasure(previewLine.calculated_base_quantity, previewLine.base_unit)}`} ·
              applying {formatMeasure(previewLine.applied_base_quantity, previewLine.base_unit)}
              {previewLines.length > 1 ? ` to each of ${previewLines.length} fills` : ''}
            </div>
            <div className="small">
              Lot holds {formatQuantity(previewLine.available_base_quantity)} and would hold {formatQuantity(finalLine.available_after_base_quantity)} {previewLine.base_unit}
            </div>
            {short && <div className="fw-bold">This lot does not hold enough.</div>}
          </Alert>
        )}

        {overrideRequired && (
          <Form.Group className="mb-3" controlId="application-override-reason">
            <Form.Label>Why does this differ from the calculation?</Form.Label>
            <Form.Control value={overrideReason} onChange={(event) => setOverrideReason(event.target.value)} />
          </Form.Group>
        )}

        {error && <Alert variant="danger">{error}</Alert>}

        <div className="d-flex gap-2">
          <Button variant="outline-primary" disabled={!readyToCheck || checkMutation.isPending} onClick={() => checkMutation.mutate()}>
            {checkMutation.isPending ? 'Checking…' : preview ? 'Re-check' : 'Check'}
          </Button>
          <Button disabled={!readyToPost || postMutation.isPending} onClick={() => postMutation.mutate()}>
            {postMutation.isPending ? 'Posting…' : 'Post application'}
          </Button>
          {draft && (
            <Button variant="outline-secondary" disabled={discardMutation.isPending} onClick={() => discardMutation.mutate()}>
              Discard draft
            </Button>
          )}
        </div>
      </Card.Body>
    </Card>
  )
}

export { ApplicationTargetOption, InputApplicationForm }
