import React from 'react'
import { useMutation } from '@tanstack/react-query'
import { Alert, Button, Card, Col, Form, Row } from 'react-bootstrap'

import { createLabelTemplate } from '../api/labels'
import { LabelDimensions, LabelFormat, LabelLayout, LabelPayloadMode, LabelTemplate } from '../types/labels'
import { errorsByField } from '../utils'
import { sheetLayout } from './sheet'

// The fields a label may carry, in the order the server knows them by
// (`LABEL_FIELDS` in `labels/rest.py`). The list is the template's, not the
// target's: a pot has no variety and a plant has no container size, and a
// value a target cannot supply is simply left off its label.
const TEMPLATE_FIELDS: ReadonlyArray<readonly [string, string]> = [
  ['display', 'Name'],
  ['variety', 'Variety'],
  ['batch', 'Batch'],
  ['sowing_date', 'Sowing date'],
  ['expected_ready', 'Expected ready'],
  ['quantity', 'Quantity'],
  ['stage', 'Growth stage'],
  ['grade', 'Grade'],
  ['container', 'Container'],
  ['container_count', 'Container count'],
  ['code', 'Label code'],
  ['print_date', 'Print date']
]

const LAYOUTS: ReadonlyArray<readonly [LabelLayout, string]> = [
  ['sheet', 'Sheet of labels'],
  ['roll', 'Roll printer'],
  ['single', 'One label at a time']
]

// Known stock, so the common case is one click rather than seven measurements
// typed off the back of a box. Only geometry that has been checked against the
// paper belongs here: a preset is a claim about somebody's physical sheet, and
// a wrong one wastes the box rather than merely being inconvenient. LC24 is
// self-checking — three 70mm labels are exactly the 210mm of A4 and eight 36mm
// rows leave the 9mm its top and bottom bars split.
const PRESETS: ReadonlyArray<readonly [string, LabelDimensions]> = [
  ['LC24 — 70 × 36 mm, 3 × 8', { label_width_mm: 70, label_height_mm: 36, page_width_mm: 210, page_height_mm: 297, margin_mm: 0, margin_top_mm: 4.5, gap_mm: 0, row_gap_mm: 0 }]
]

const BLANK: LabelDimensions = {
  label_width_mm: 70,
  label_height_mm: 36,
  page_width_mm: 210,
  page_height_mm: 297,
  margin_mm: 0,
  margin_top_mm: 0,
  gap_mm: 0,
  row_gap_mm: 0
}

interface TemplateFormProps {
  onCreated: (template: LabelTemplate) => void
}

// Designing a template is measuring a sheet, so the form asks for the sheet
// and says what it works out to before anything is saved. A template is only
// ever added here: the built-ins are not editable, and a sheet already printed
// against stays as it is so a reprint lands where the first print did.
function TemplateForm({ onCreated }: TemplateFormProps) {
  const [open, setOpen] = React.useState(false)
  const [name, setName] = React.useState('')
  const [format, setFormat] = React.useState<LabelFormat>('qr')
  const [payloadMode, setPayloadMode] = React.useState<LabelPayloadMode>('url')
  const [layout, setLayout] = React.useState<LabelLayout>('sheet')
  const [dimensions, setDimensions] = React.useState<LabelDimensions>(BLANK)
  const [fields, setFields] = React.useState<Array<string>>(['display', 'container', 'code'])
  const [error, setError] = React.useState<string>()

  // Code 128 carries the bare code and nothing else, which the server enforces
  // too — said here so the control cannot offer a choice about to be refused.
  const linear = format === 'code128'
  const mode: LabelPayloadMode = linear ? 'code' : payloadMode
  const sheet = layout === 'sheet'
  const geometry = sheetLayout({ layout, dimensions })

  const mutation = useMutation({
    mutationFn: () =>
      createLabelTemplate({
        name: name.trim(),
        format,
        payload_mode: mode,
        layout,
        fields,
        // A roll or a single label has no page to sit on, so it states the
        // label alone rather than carrying a sheet's figures it never reads.
        dimensions: sheet ? dimensions : { label_width_mm: dimensions.label_width_mm, label_height_mm: dimensions.label_height_mm }
      }),
    onSuccess: (created) => {
      setName('')
      setError(undefined)
      setOpen(false)
      onCreated(created)
    },
    onError: (caught: unknown) => {
      const byField = errorsByField(caught)
      setError(byField.dimensions ?? byField.name ?? byField.fields ?? byField.payload_mode ?? (caught instanceof Error ? caught.message : String(caught)))
    }
  })

  function setNumber(key: keyof LabelDimensions, value: string) {
    setDimensions({ ...dimensions, [key]: value === '' ? 0 : Number(value) })
  }

  function toggleField(field: string) {
    setFields(fields.includes(field) ? fields.filter((entry) => entry !== field) : [...fields, field])
  }

  const measured = dimensions.label_width_mm > 0 && dimensions.label_height_mm > 0
  const usable = measured && fields.length > 0 && name.trim().length > 0 && (!sheet || geometry.perPage > 0)

  if (!open) {
    return (
      <Button variant="outline-primary" className="mb-3" onClick={() => setOpen(true)}>
        New template
      </Button>
    )
  }

  return (
    <Card body className="mb-3">
      <Card.Title>New template</Card.Title>
      <Row className="g-2 mb-2">
        <Col md={5}>
          <Form.Label htmlFor="template-name">Name</Form.Label>
          <Form.Control id="template-name" value={name} onChange={(event) => setName(event.target.value)} />
        </Col>
        <Col md={3}>
          <Form.Label htmlFor="template-layout">Layout</Form.Label>
          <Form.Select id="template-layout" value={layout} onChange={(event) => setLayout(event.target.value as LabelLayout)}>
            {LAYOUTS.map(([value, label]) => (
              <option value={value} key={value}>
                {label}
              </option>
            ))}
          </Form.Select>
        </Col>
        <Col md={2}>
          <Form.Label htmlFor="template-format">Code</Form.Label>
          <Form.Select id="template-format" value={format} onChange={(event) => setFormat(event.target.value as LabelFormat)}>
            <option value="qr">QR code</option>
            <option value="code128">Code 128</option>
          </Form.Select>
        </Col>
        <Col md={2}>
          <Form.Label htmlFor="template-payload">Payload</Form.Label>
          <Form.Select id="template-payload" value={mode} disabled={linear} onChange={(event) => setPayloadMode(event.target.value as LabelPayloadMode)}>
            <option value="url">App deep link</option>
            <option value="code">Bare code</option>
          </Form.Select>
        </Col>
      </Row>
      {sheet && (
        <Row className="g-2 mb-2">
          <Col md={12}>
            <Form.Label htmlFor="template-preset">Start from known stock</Form.Label>
            <Form.Select
              id="template-preset"
              value=""
              onChange={(event) => {
                const preset = PRESETS.find(([label]) => label === event.target.value)
                if (preset) setDimensions(preset[1])
              }}
            >
              <option value="">Measure it myself</option>
              {PRESETS.map(([label]) => (
                <option value={label} key={label}>
                  {label}
                </option>
              ))}
            </Form.Select>
          </Col>
        </Row>
      )}
      <Row className="g-2 mb-2">
        <Col md={2}>
          <Form.Label htmlFor="template-width">Label width (mm)</Form.Label>
          <Form.Control
            id="template-width"
            type="number"
            min="1"
            step="0.1"
            value={dimensions.label_width_mm}
            onChange={(event) => setNumber('label_width_mm', event.target.value)}
          />
        </Col>
        <Col md={2}>
          <Form.Label htmlFor="template-height">Label height (mm)</Form.Label>
          <Form.Control
            id="template-height"
            type="number"
            min="1"
            step="0.1"
            value={dimensions.label_height_mm}
            onChange={(event) => setNumber('label_height_mm', event.target.value)}
          />
        </Col>
        {sheet && (
          <>
            <Col md={2}>
              <Form.Label htmlFor="template-page-width">Page width (mm)</Form.Label>
              <Form.Control
                id="template-page-width"
                type="number"
                min="1"
                step="0.1"
                value={dimensions.page_width_mm ?? 0}
                onChange={(event) => setNumber('page_width_mm', event.target.value)}
              />
            </Col>
            <Col md={2}>
              <Form.Label htmlFor="template-page-height">Page height (mm)</Form.Label>
              <Form.Control
                id="template-page-height"
                type="number"
                min="1"
                step="0.1"
                value={dimensions.page_height_mm ?? 0}
                onChange={(event) => setNumber('page_height_mm', event.target.value)}
              />
            </Col>
            <Col md={2}>
              <Form.Label htmlFor="template-margin">Side margin (mm)</Form.Label>
              <Form.Control
                id="template-margin"
                type="number"
                min="0"
                step="0.1"
                value={dimensions.margin_mm ?? 0}
                onChange={(event) => setNumber('margin_mm', event.target.value)}
              />
            </Col>
            <Col md={2}>
              <Form.Label htmlFor="template-margin-top">Top bar (mm)</Form.Label>
              <Form.Control
                id="template-margin-top"
                type="number"
                min="0"
                step="0.1"
                value={dimensions.margin_top_mm ?? 0}
                onChange={(event) => setNumber('margin_top_mm', event.target.value)}
              />
            </Col>
            <Col md={2}>
              <Form.Label htmlFor="template-gap">Gap across (mm)</Form.Label>
              <Form.Control id="template-gap" type="number" min="0" step="0.1" value={dimensions.gap_mm ?? 0} onChange={(event) => setNumber('gap_mm', event.target.value)} />
            </Col>
            <Col md={2}>
              <Form.Label htmlFor="template-row-gap">Gap down (mm)</Form.Label>
              <Form.Control
                id="template-row-gap"
                type="number"
                min="0"
                step="0.1"
                value={dimensions.row_gap_mm ?? 0}
                onChange={(event) => setNumber('row_gap_mm', event.target.value)}
              />
            </Col>
          </>
        )}
      </Row>
      <Form.Label>Printed on each label</Form.Label>
      <div className="d-flex flex-wrap gap-3 mb-2">
        {TEMPLATE_FIELDS.map(([value, label]) => (
          <Form.Check key={value} type="checkbox" id={`template-field-${value}`} label={label} checked={fields.includes(value)} onChange={() => toggleField(value)} />
        ))}
      </div>
      {sheet && measured && (
        <p className="text-muted small mb-2">
          {geometry.perPage > 0 ? `${geometry.columns} across × ${geometry.rows} down — ${geometry.perPage} per sheet.` : 'One label and its margins do not fit the page.'}
        </p>
      )}
      <div className="d-flex gap-2">
        <Button disabled={!usable || mutation.isPending} onClick={() => mutation.mutate()}>
          {mutation.isPending ? 'Saving…' : 'Save template'}
        </Button>
        <Button variant="outline-secondary" onClick={() => setOpen(false)}>
          Cancel
        </Button>
      </div>
      {error && (
        <Alert className="mt-2 mb-0" variant="danger">
          {error}
        </Alert>
      )}
    </Card>
  )
}

export { TEMPLATE_FIELDS, TemplateForm }
