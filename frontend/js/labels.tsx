import React from 'react'
import bwipjs from 'bwip-js/browser'
import { BrowserMultiFormatReader, IScannerControls } from '@zxing/browser'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Alert, Badge, Button, Card, Col, Form, Row, Table } from 'react-bootstrap'
import { Link, useParams } from 'react-router'

import { createLabelPrintJob, getLabelIdentities, getLabelTemplates, markLabelPrintJobPrinted, previewLabels, resolveLabel } from './api/labels'
import { getLocations } from './api/locations'
import { queryKeys } from './query'
import { LabelFormat, LabelPrintJob, LabelResolution } from './types/labels'
import { BulkOperationPanel } from './plantings/bulk_operations'

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
                <div key={field} className={field === 'code' ? 'label-code' : undefined}>
                  {field === 'display' ? <strong>{String(value)}</strong> : `${field.replaceAll('_', ' ')}: ${String(value)}`}
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
  const video = React.useRef<HTMLVideoElement>(null)
  const controls = React.useRef<IScannerControls | undefined>(undefined)
  const locations = useQuery({ queryKey: queryKeys.locations.list('active'), queryFn: ({ signal }) => getLocations(signal, true) })

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
