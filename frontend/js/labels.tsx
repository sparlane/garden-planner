import React from 'react'
import bwipjs from 'bwip-js/browser'
import { BrowserMultiFormatReader, IScannerControls } from '@zxing/browser'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Alert, Badge, Button, Card, Col, Form, Row, Table } from 'react-bootstrap'
import { Link, useParams } from 'react-router'

import { createLabelPrintJob, createLabelTemplate, getLabelIdentities, getLabelTemplates, markLabelPrintJobPrinted, previewLabels, resolveLabel } from './api/labels'
import { getLocations } from './api/locations'
import { queryKeys } from './query'
import { LabelFormat, LabelPrintJob, LabelResolution } from './types/labels'
import { BulkOperationPanel } from './plantings/bulk_operations'
import { HealthScopeType } from './types/health'
import { allocateOrderLine, getFulfillments, getSalesOrders, postFulfillment, postReturn, previewAllocation } from './api/sales'

import './labels.css'

function Barcode({ format, payload }: { format: LabelFormat; payload: string }) {
  const svg = bwipjs.toSVG({ bcid: format === 'qr' ? 'qrcode' : 'code128', text: payload, scale: 2, includetext: false })
  return <div aria-label={`${format === 'qr' ? 'QR' : 'Code 128'} code`} dangerouslySetInnerHTML={{ __html: svg }} />
}

function PrintArea({ job }: { job: LabelPrintJob }) {
  const dimensions = job.template.dimensions
  const width = `${dimensions.label_width_mm}mm`
  const height = `${dimensions.label_height_mm}mm`
  const columns = job.template.layout === 'sheet' ? `repeat(auto-fill, ${width})` : width
  return (
    <div className="label-print-area label-sheet" style={{ gridTemplateColumns: columns, ['--label-gap' as string]: `${dimensions.gap_mm ?? 0}mm` }}>
      {job.items.map((item) => (
        <article className="physical-label" key={`${item.identity}:${item.position}`} style={{ width, height }}>
          <Barcode format={job.template.format} payload={item.payload} />
          <div className="label-values">
            {job.template.fields.map((field) => {
              const value = item.target[field]
              if (value === undefined || value === null || value === '') return null
              return (
                <div key={field}>
                  {field === 'display' ? (
                    <strong>{String(value)}</strong>
                  ) : field === 'code' ? (
                    <>
                      <div>code:</div>
                      <div className="label-code">{String(value)}</div>
                    </>
                  ) : (
                    `${field.replaceAll('_', ' ')}: ${String(value)}`
                  )}
                </div>
              )
            })}
            {item.is_reprint && <small>Reprint</small>}
          </div>
        </article>
      ))}
    </div>
  )
}

function LabelsView() {
  const [selected, setSelected] = React.useState<Array<number>>([])
  const [templatePk, setTemplatePk] = React.useState<number | ''>('')
  const [payloadMode, setPayloadMode] = React.useState<'code' | 'url'>('url')
  const [preview, setPreview] = React.useState<LabelPrintJob>()
  const [templateName, setTemplateName] = React.useState('')
  const [labelWidth, setLabelWidth] = React.useState('50')
  const [labelHeight, setLabelHeight] = React.useState('30')
  const identities = useQuery({ queryKey: ['labels', 'identities'], queryFn: ({ signal }) => getLabelIdentities(signal) })
  const templates = useQuery({ queryKey: ['labels', 'templates'], queryFn: ({ signal }) => getLabelTemplates(signal) })
  const selectedTemplate = templates.data?.find((entry) => entry.pk === templatePk)
  const previewMutation = useMutation({ mutationFn: () => previewLabels(templatePk as number, selected, payloadMode), onSuccess: setPreview })
  const printMutation = useMutation({
    mutationFn: () => createLabelPrintJob(templatePk as number, selected, payloadMode),
    onSuccess: async (job) => {
      setPreview(job)
      if (job.job !== null) {
        await markLabelPrintJobPrinted(job.job)
        window.setTimeout(() => window.print(), 0)
      }
    }
  })
  const templateMutation = useMutation({
    mutationFn: () =>
      createLabelTemplate({
        name: templateName,
        format: 'qr',
        payload_mode: 'url',
        layout: 'roll',
        fields: ['display', 'variety', 'batch', 'sowing_date', 'expected_ready', 'code', 'print_date'],
        dimensions: { label_width_mm: Number(labelWidth), label_height_mm: Number(labelHeight) }
      }),
    onSuccess: async (created) => {
      setTemplatePk(created.pk)
      setTemplateName('')
      await templates.refetch()
    }
  })

  React.useEffect(() => {
    if (templatePk === '' && templates.data?.length) setTemplatePk(templates.data[0].pk)
  }, [templatePk, templates.data])

  React.useEffect(() => {
    if (selectedTemplate?.format === 'code128') setPayloadMode('code')
    else if (selectedTemplate) setPayloadMode(selectedTemplate.payload_mode)
    setPreview(undefined)
  }, [selectedTemplate])

  function toggle(identity: number) {
    setSelected(selected.includes(identity) ? selected.filter((entry) => entry !== identity) : [...selected, identity])
    setPreview(undefined)
  }

  return (
    <main className="container py-3">
      <div className="no-print">
        <h1>Labels</h1>
        <p>Select physical records, preview their durable labels, then initiate a browser print job.</p>
        <Row className="g-2 mb-3">
          <Col md={6}>
            <Form.Label>Template</Form.Label>
            <Form.Select value={templatePk} onChange={(event) => setTemplatePk(Number(event.target.value))}>
              {(templates.data ?? [])
                .filter((entry) => entry.active)
                .map((entry) => (
                  <option value={entry.pk} key={entry.pk}>
                    {entry.name}
                  </option>
                ))}
            </Form.Select>
          </Col>
          <Col md={3}>
            <Form.Label>QR payload</Form.Label>
            <Form.Select value={payloadMode} disabled={selectedTemplate?.format === 'code128'} onChange={(event) => setPayloadMode(event.target.value as 'code' | 'url')}>
              <option value="url">App deep link</option>
              <option value="code">Bare code</option>
            </Form.Select>
          </Col>
        </Row>
        <Card body className="mb-3">
          <Card.Title>Save a custom roll template</Card.Title>
          <Row className="g-2 align-items-end">
            <Col md={5}>
              <Form.Label>Name</Form.Label>
              <Form.Control value={templateName} onChange={(event) => setTemplateName(event.target.value)} />
            </Col>
            <Col md={2}>
              <Form.Label>Width (mm)</Form.Label>
              <Form.Control type="number" min="1" step="0.1" value={labelWidth} onChange={(event) => setLabelWidth(event.target.value)} />
            </Col>
            <Col md={2}>
              <Form.Label>Height (mm)</Form.Label>
              <Form.Control type="number" min="1" step="0.1" value={labelHeight} onChange={(event) => setLabelHeight(event.target.value)} />
            </Col>
            <Col md={3}>
              <Button
                disabled={!templateName.trim() || Number(labelWidth) <= 0 || Number(labelHeight) <= 0 || templateMutation.isPending}
                onClick={() => templateMutation.mutate()}
              >
                Save template
              </Button>
            </Col>
          </Row>
        </Card>
        <Table responsive hover size="sm">
          <thead>
            <tr>
              <th>Select</th>
              <th>Type</th>
              <th>Record</th>
              <th>Code</th>
            </tr>
          </thead>
          <tbody>
            {(identities.data ?? []).map((identity) => (
              <tr key={identity.identity}>
                <td>
                  <Form.Check aria-label={`Select ${identity.display}`} checked={selected.includes(identity.identity)} onChange={() => toggle(identity.identity)} />
                </td>
                <td>{identity.target_type}</td>
                <td>{identity.display}</td>
                <td className="font-monospace">{identity.code}</td>
              </tr>
            ))}
          </tbody>
        </Table>
        <div className="d-flex gap-2 mb-3">
          <Button variant="outline-primary" disabled={!templatePk || selected.length === 0 || previewMutation.isPending} onClick={() => previewMutation.mutate()}>
            Preview
          </Button>
          <Button disabled={!templatePk || selected.length === 0 || printMutation.isPending} onClick={() => printMutation.mutate()}>
            Print {selected.length} label{selected.length === 1 ? '' : 's'}
          </Button>
        </div>
      </div>
      {preview && <PrintArea job={preview} />}
    </main>
  )
}

function ScanResult({ result }: { result: LabelResolution }) {
  const variant = result.status === 'active' ? 'success' : result.status === 'unknown' || result.status === 'wrong_workspace' ? 'danger' : 'warning'
  return (
    <Alert variant={variant}>
      <strong>{result.message}</strong>
      {result.target && (
        <div>
          {result.target.display} <span className="font-monospace">{result.current_code ?? result.code}</span>
        </div>
      )}
      {result.deep_link && (
        <Link to={result.deep_link}>
          <Button className="mt-2" size="lg">
            Open record
          </Button>
        </Link>
      )}
    </Alert>
  )
}

function ScannerView() {
  const { code } = useParams()
  const [value, setValue] = React.useState(code ?? '')
  const [result, setResult] = React.useState<LabelResolution>()
  const [cameraError, setCameraError] = React.useState('')
  const [scanned, setScanned] = React.useState<Array<{ id: number; code: string; display: string }>>([])
  const [healthScanned, setHealthScanned] = React.useState<Array<{ type: HealthScopeType; id: number; code: string; display: string }>>([])
  const [stocktakeCode, setStocktakeCode] = React.useState('')
  const [salesOrder, setSalesOrder] = React.useState<number | ''>('')
  const [salesLine, setSalesLine] = React.useState<number | ''>('')
  const [orderMode, setOrderMode] = React.useState<'allocate' | 'fulfill' | 'return'>('allocate')
  const [orderScanned, setOrderScanned] = React.useState<Array<{ id: number; code: string; display: string }>>([])
  const [returnDestination, setReturnDestination] = React.useState<number | ''>('')
  const [orderConflict, setOrderConflict] = React.useState('')
  const video = React.useRef<HTMLVideoElement>(null)
  const controls = React.useRef<IScannerControls | undefined>(undefined)
  const locations = useQuery({ queryKey: queryKeys.locations.list('active'), queryFn: ({ signal }) => getLocations(signal, true) })
  const orders = useQuery({ queryKey: queryKeys.sales.orders, queryFn: ({ signal }) => getSalesOrders(signal) })
  const chosenOrder = orders.data?.find((entry) => entry.pk === salesOrder)
  const chosenLine = chosenOrder?.lines.find((entry) => entry.pk === salesLine)
  const scannedFulfillments = useQuery({
    queryKey: ['sales', salesOrder, 'scan-fulfillments'],
    queryFn: ({ signal }) => getFulfillments(salesOrder as number, signal),
    enabled: salesOrder !== '' && orderMode === 'return'
  })
  const orderAllocation = useMutation({
    mutationFn: async (resolved: LabelResolution) => {
      if (!chosenOrder || !chosenLine || !resolved.target) throw new Error('Choose an order line before scanning stock.')
      const id = chosenLine.line_type === 'seedling' ? resolved.target.object_id : resolved.target.inventory_unit
      if (id === undefined) throw new Error('That label is not compatible with the selected line.')
      const preview = await previewAllocation(chosenOrder.pk, {
        line: chosenLine.pk,
        ...(chosenLine.line_type === 'seedling' ? { plant_ids: [id] } : { unit_ids: [id] })
      })
      if (preview.conflicts.length) throw new Error(preview.conflicts[0].reason.replaceAll('_', ' '))
      return { id, code: resolved.current_code ?? resolved.code ?? '', display: resolved.target.display }
    },
    onSuccess: (entry) => {
      setOrderConflict('')
      setOrderScanned((current) => (current.some((item) => item.id === entry.id) ? current : [...current, entry]))
    },
    onError: (error) => setOrderConflict(error instanceof Error ? error.message : String(error))
  })
  const saveOrderAllocation = useMutation({
    mutationFn: () =>
      allocateOrderLine(
        chosenOrder?.pk as number,
        chosenLine?.pk as number,
        chosenLine?.line_type === 'seedling' ? orderScanned.map((entry) => entry.id) : [],
        chosenLine?.line_type === 'tray' ? orderScanned.map((entry) => entry.id) : [],
        null
      ),
    onSuccess: () => {
      setOrderScanned([])
      void orders.refetch()
    }
  })
  const saveCommerceScans = useMutation<object>({
    mutationFn: async () => {
      if (!chosenOrder) throw new Error('Choose an order first.')
      if (orderMode === 'fulfill') {
        return await postFulfillment(chosenOrder.pk, { operation_key: crypto.randomUUID(), allocation_ids: orderScanned.map((entry) => entry.id) })
      }
      return await postReturn(chosenOrder.pk, {
        operation_key: crypto.randomUUID(),
        reason: 'Returned through label scan.',
        items: orderScanned.map((entry) => ({ fulfillment_line: entry.id, outcome: 'available', destination: returnDestination }))
      })
    },
    onSuccess: () => {
      setOrderScanned([])
      void orders.refetch()
      void scannedFulfillments.refetch()
    }
  })

  const resolveMutation = useMutation({
    mutationFn: (input: string) => resolveLabel(input),
    onSuccess: (resolved) => {
      setResult(resolved)
      if (resolved.status === 'active' && resolved.target && resolved.capabilities?.includes('bulk_select')) {
        const target = resolved.target
        setScanned((current) =>
          current.some((entry) => entry.id === target.object_id)
            ? current
            : [...current, { id: target.object_id, code: resolved.current_code ?? resolved.code ?? '', display: target.display }]
        )
      }
      if (resolved.status === 'active' && resolved.target && resolved.capabilities?.includes('order_allocate') && chosenLine) {
        if (orderMode === 'allocate') orderAllocation.mutate(resolved)
      }
      if (resolved.status === 'active' && resolved.target && chosenOrder && orderMode !== 'allocate') {
        const target = resolved.target
        const allocation = chosenOrder.lines
          .flatMap((line) => line.allocations)
          .find((entry) => entry.plant === (target.target_type === 'specificplant' ? target.object_id : null) || entry.inventory_unit === target.inventory_unit)
        if (orderMode === 'fulfill' && resolved.capabilities?.includes('order_fulfill') && allocation?.status === 'reserved') {
          setOrderScanned((current) =>
            current.some((entry) => entry.id === allocation.pk)
              ? current
              : [...current, { id: allocation.pk, code: resolved.current_code ?? resolved.code ?? '', display: target.display }]
          )
        } else if (orderMode === 'return' && resolved.capabilities?.includes('order_return') && allocation) {
          const fulfillmentLine = (scannedFulfillments.data ?? [])
            .filter((entry) => entry.status === 'posted')
            .flatMap((entry) => entry.lines)
            .find((line) => line.allocation === allocation.pk)
          if (fulfillmentLine)
            setOrderScanned((current) =>
              current.some((entry) => entry.id === fulfillmentLine.pk)
                ? current
                : [...current, { id: fulfillmentLine.pk, code: resolved.current_code ?? resolved.code ?? '', display: target.display }]
            )
        } else {
          setOrderConflict(`That label is not eligible for ${orderMode} on the chosen order.`)
        }
      }
      if (resolved.status === 'active' && resolved.target && resolved.capabilities?.includes('health_inspection')) {
        const target = resolved.target
        const scopes: Record<string, HealthScopeType> = {
          specificplant: 'plant',
          plantcohort: 'cohort',
          seedtray: 'tray',
          productionbatch: 'batch',
          location: 'location'
        }
        if (resolved.status === 'active' && resolved.capabilities?.includes('stocktake_count')) setStocktakeCode(resolved.current_code ?? resolved.code ?? '')
        const type = scopes[target.target_type]
        if (type) {
          setHealthScanned((current) =>
            current.some((entry) => entry.type === type && entry.id === target.object_id)
              ? current
              : [...current, { type, id: target.object_id, code: resolved.current_code ?? resolved.code ?? '', display: target.display }]
          )
        }
      }
    }
  })

  function submit(next = value) {
    const clean = next.trim()
    if (clean) resolveMutation.mutate(clean)
  }

  async function startCamera() {
    setCameraError('')
    try {
      const reader = new BrowserMultiFormatReader()
      controls.current = await reader.decodeFromVideoDevice(undefined, video.current ?? undefined, (decoded) => {
        if (decoded) {
          const text = decoded.getText()
          setValue(text)
          submit(text)
        }
      })
    } catch (error) {
      setCameraError(error instanceof Error ? error.message : String(error))
    }
  }

  React.useEffect(() => {
    if (code) submit(code)
    return () => controls.current?.stop()
  }, [])

  const ids = scanned.map((entry) => entry.id)
  return (
    <main className="container py-3">
      <h1>Scan labels</h1>
      <Row className="g-3">
        <Col lg={6}>
          {stocktakeCode && (
            <Alert variant="info">
              <strong>Stocktake-ready identity</strong>
              <div className="font-monospace">{stocktakeCode}</div>
              <Link to="/inventory/stocktakes">
                <Button className="mt-2">Open stocktakes</Button>
              </Link>
            </Alert>
          )}
          <Card body>
            <video ref={video} className="scanner-video" muted playsInline />
            <div className="d-flex gap-2 mt-2">
              <Button size="lg" onClick={startCamera}>
                Start camera
              </Button>
              <Button size="lg" variant="outline-secondary" onClick={() => controls.current?.stop()}>
                Stop
              </Button>
            </div>
            {cameraError && (
              <Alert variant="warning" className="mt-2">
                Camera unavailable: {cameraError}. Enter the code below or use a hardware scanner.
              </Alert>
            )}
            <Form
              className="mt-3"
              onSubmit={(event) => {
                event.preventDefault()
                submit()
              }}
            >
              <Form.Label>Label code or QR URL</Form.Label>
              <div className="d-flex gap-2">
                <Form.Control size="lg" autoFocus value={value} onChange={(event) => setValue(event.target.value)} />
                <Button size="lg" type="submit">
                  Resolve
                </Button>
              </div>
            </Form>
          </Card>
          {result && (
            <div className="mt-3">
              <ScanResult result={result} />
            </div>
          )}
        </Col>
        <Col lg={6}>
          <h2>
            Order commerce <Badge bg="primary">{orderScanned.length}</Badge>
          </h2>
          <Row className="g-2 mb-2">
            <Col md={4}>
              <Form.Select
                value={orderMode}
                onChange={(event) => {
                  setOrderMode(event.target.value as typeof orderMode)
                  setOrderScanned([])
                  setOrderConflict('')
                }}
              >
                <option value="allocate">Allocate</option>
                <option value="fulfill">Fulfill</option>
                <option value="return">Return available</option>
              </Form.Select>
            </Col>
            <Col md={4}>
              <Form.Select
                value={salesOrder}
                onChange={(event) => {
                  setSalesOrder(event.target.value === '' ? '' : Number(event.target.value))
                  setSalesLine('')
                  setOrderScanned([])
                }}
              >
                <option value="">Choose an order</option>
                {(orders.data ?? [])
                  .filter((entry) => ['quote', 'draft', 'confirmed', 'partially_fulfilled'].includes(entry.status))
                  .map((entry) => (
                    <option key={entry.pk} value={entry.pk}>
                      {entry.order_number}
                    </option>
                  ))}
              </Form.Select>
            </Col>
            <Col md={4}>
              <Form.Select
                value={salesLine}
                disabled={!chosenOrder || orderMode !== 'allocate'}
                onChange={(event) => {
                  setSalesLine(event.target.value === '' ? '' : Number(event.target.value))
                  setOrderScanned([])
                }}
              >
                <option value="">Choose a line before scanning</option>
                {(chosenOrder?.lines ?? []).map((line) => (
                  <option key={line.pk} value={line.pk}>
                    {line.description} ({line.allocations.filter((allocation) => ['pending', 'reserved'].includes(allocation.status)).length}/{line.quantity})
                  </option>
                ))}
              </Form.Select>
            </Col>
          </Row>
          {orderConflict && <Alert variant="warning">Scan not added: {orderConflict}</Alert>}
          {orderMode === 'allocate' && chosenLine && (
            <p className="text-muted">Scan {chosenLine.line_type === 'seedling' ? 'plant' : 'tray'} labels to validate them against this line.</p>
          )}
          {orderMode === 'fulfill' && chosenOrder && (
            <p className="text-muted">Scans stage reserved plants or trays from this order. Posting happens only when you confirm below.</p>
          )}
          {orderMode === 'return' && chosenOrder && (
            <Form.Select className="mb-2" value={returnDestination} onChange={(event) => setReturnDestination(event.target.value === '' ? '' : Number(event.target.value))}>
              <option value="">Return destination</option>
              {(locations.data ?? [])
                .filter((location) => location.location_type !== 'quarantine')
                .map((location) => (
                  <option key={location.pk} value={location.pk}>
                    {location.full_name}
                  </option>
                ))}
            </Form.Select>
          )}
          {orderScanned.length > 0 && (
            <>
              <ul className="list-group mb-2">
                {orderScanned.map((entry) => (
                  <li className="list-group-item d-flex justify-content-between" key={entry.id}>
                    <span>
                      {entry.display}
                      <br />
                      <small className="font-monospace">{entry.code}</small>
                    </span>
                    <Button variant="outline-danger" onClick={() => setOrderScanned(orderScanned.filter((item) => item.id !== entry.id))}>
                      Remove
                    </Button>
                  </li>
                ))}
              </ul>
              <Button
                className="mb-4"
                disabled={
                  orderMode === 'allocate'
                    ? !chosenLine ||
                      orderScanned.length + (chosenLine?.allocations.filter((allocation) => ['pending', 'reserved'].includes(allocation.status)).length ?? 0) >
                        (chosenLine?.quantity ?? 0)
                    : orderMode === 'return' && returnDestination === ''
                }
                onClick={() => (orderMode === 'allocate' ? saveOrderAllocation.mutate() : saveCommerceScans.mutate())}
              >
                {orderMode === 'allocate' ? 'Add scans to order' : orderMode === 'fulfill' ? 'Post scanned fulfillment' : 'Post scanned return'}
              </Button>
            </>
          )}
          <h2>
            Health inspection{' '}
            <Badge bg="warning" text="dark">
              {healthScanned.length}
            </Badge>
          </h2>
          {healthScanned.length === 0 ? (
            <p className="text-muted">Eligible nursery labels scanned here will build an exact health-inspection scope.</p>
          ) : (
            <>
              <ul className="list-group mb-3">
                {healthScanned.map((entry) => (
                  <li className="list-group-item d-flex justify-content-between" key={`${entry.type}:${entry.id}`}>
                    <span>
                      {entry.display}
                      <br />
                      <small className="font-monospace">{entry.code}</small>
                    </span>
                    <Button variant="outline-danger" onClick={() => setHealthScanned(healthScanned.filter((item) => item !== entry))}>
                      Remove
                    </Button>
                  </li>
                ))}
              </ul>
              <Link to={`/health?${healthScanned.map((entry) => `scope=${encodeURIComponent(`${entry.type}:${entry.id}`)}`).join('&')}`}>
                <Button variant="warning" className="mb-4">
                  Review health scope
                </Button>
              </Link>
            </>
          )}
          <h2>
            Plant selection <Badge bg="secondary">{scanned.length}</Badge>
          </h2>
          {scanned.length === 0 ? (
            <p className="text-muted">Active plant labels scanned here will build a reviewed bulk selection.</p>
          ) : (
            <>
              <ul className="list-group mb-3">
                {scanned.map((entry) => (
                  <li className="list-group-item d-flex justify-content-between" key={entry.id}>
                    <span>
                      {entry.display}
                      <br />
                      <small className="font-monospace">{entry.code}</small>
                    </span>
                    <Button variant="outline-danger" onClick={() => setScanned(scanned.filter((item) => item.id !== entry.id))}>
                      Remove
                    </Button>
                  </li>
                ))}
              </ul>
              <BulkOperationPanel
                selection={{ mode: 'ids', ids }}
                filters={{}}
                locations={locations.data ?? []}
                setSelection={() => setScanned([])}
                sourceLabels={scanned.map((entry) => entry.code)}
              />
            </>
          )}
        </Col>
      </Row>
    </main>
  )
}

export { LabelsView, ScannerView }
