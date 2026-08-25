import React from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Badge, Button, Card, Col, Form, Row, Table } from 'react-bootstrap'
import { useSearchParams } from 'react-router'

import { AttachmentGallery, PhotoInput } from './attachments'
import { uploadAttachments } from './api/attachments'
import {
  actOnQuarantine,
  correctHealthObservation,
  createHealthObservation,
  getHealthDiagnoses,
  getHealthObservationTypes,
  getHealthObservations,
  getHealthReport,
  getQuarantineCases,
  linkHealthTreatment,
  previewHealthObservation,
  quarantineHealthObservation,
  recordHealthFollowUp
} from './api/health'
import { getLocations } from './api/locations'
import { queryKeys } from './query'
import { HealthObservation, HealthPreview, HealthScope, HealthScopeType, HealthSeverity, QuarantineCase } from './types/health'
import { formatDateTime } from './utils'

const SCOPE_LABELS: Record<HealthScopeType, string> = {
  plant: 'Plant',
  cohort: 'Cohort',
  tray: 'Tray (current fill)',
  generation: 'Tray fill',
  batch: 'Batch',
  location: 'Location and descendants'
}

const SEVERITIES: Array<HealthSeverity> = ['low', 'moderate', 'high', 'critical']

function invalidateHealth(cache: ReturnType<typeof useQueryClient>) {
  return Promise.all([
    cache.invalidateQueries({ queryKey: queryKeys.health.all }),
    cache.invalidateQueries({ queryKey: queryKeys.plantings.registerAll }),
    cache.invalidateQueries({ queryKey: queryKeys.plantings.cohortsAll }),
    cache.invalidateQueries({ queryKey: queryKeys.work.all })
  ])
}

function ObservationComposer({ initialScopes = [] }: { initialScopes?: Array<HealthScope> }) {
  const cache = useQueryClient()
  const { data: types = [] } = useQuery({ queryKey: queryKeys.health.types, queryFn: ({ signal }) => getHealthObservationTypes(signal) })
  const { data: diagnoses = [] } = useQuery({ queryKey: queryKeys.health.diagnoses, queryFn: ({ signal }) => getHealthDiagnoses(signal) })
  const [scopeType, setScopeType] = React.useState<HealthScopeType>('plant')
  const [scopeId, setScopeId] = React.useState<number | ''>('')
  const [scopes, setScopes] = React.useState<Array<HealthScope>>(initialScopes)
  const [preview, setPreview] = React.useState<HealthPreview>()
  const [observationType, setObservationType] = React.useState<number | ''>('')
  const [severity, setSeverity] = React.useState<HealthSeverity>('moderate')
  const [diagnosis, setDiagnosis] = React.useState<number | ''>('')
  const [certainty, setCertainty] = React.useState<'suspected' | 'confirmed'>('suspected')
  const [notes, setNotes] = React.useState('')
  const [evidenceUrl, setEvidenceUrl] = React.useState('')
  const [followUp, setFollowUp] = React.useState('')
  const [photos, setPhotos] = React.useState<Array<File>>([])
  const [photoWarning, setPhotoWarning] = React.useState('')
  const [photoTarget, setPhotoTarget] = React.useState<number | null>(null)

  React.useEffect(() => {
    if (observationType === '' && types.length > 0) setObservationType(types[0].pk)
  }, [types, observationType])

  const previewMutation = useMutation({
    mutationFn: () => previewHealthObservation(scopes),
    onSuccess: setPreview
  })
  const createMutation = useMutation({
    mutationFn: async () => {
      const observation = await createHealthObservation({
        scopes,
        reviewed_digest: preview?.digest ?? '',
        observation_type: observationType as number,
        severity,
        diagnoses: diagnosis === '' ? [] : [{ diagnosis, certainty }],
        evidence: evidenceUrl ? [{ url: evidenceUrl, label: 'Observation evidence' }] : [],
        follow_up_due_at: followUp ? new Date(followUp).toISOString() : null,
        notes
      })
      return { observation, photoResult: await uploadAttachments('health_observation', observation.pk, photos) }
    },
    onSuccess: ({ observation, photoResult }) => {
      setScopes([])
      setPreview(undefined)
      setNotes('')
      setEvidenceUrl('')
      setFollowUp('')
      setPhotos(photoResult.failures.map((failure) => failure.file))
      setPhotoWarning(photoResult.failures.length === 0 ? '' : 'The observation was recorded, but some photos failed. The failed selection is ready to retry.')
      setPhotoTarget(photoResult.failures.length === 0 ? null : observation.pk)
      void invalidateHealth(cache)
    }
  })

  function addScope() {
    if (scopeId === '') return
    const next = { type: scopeType, id: scopeId }
    if (!scopes.some((scope) => scope.type === next.type && scope.id === next.id)) {
      setScopes([...scopes, next])
      setPreview(undefined)
    }
    setScopeId('')
  }

  return (
    <Card body className="mb-3">
      <h2 className="h5">Record an inspection</h2>
      <p className="text-muted">Build a scope, review the exact living stock it resolves to, then record evidence without inferring a diagnosis.</p>
      <Row className="g-2 align-items-end">
        <Col md={4}>
          <Form.Label>Scope type</Form.Label>
          <Form.Select value={scopeType} onChange={(event) => setScopeType(event.target.value as HealthScopeType)}>
            {Object.entries(SCOPE_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </Form.Select>
        </Col>
        <Col md={4}>
          <Form.Label>Record ID</Form.Label>
          <Form.Control type="number" min={1} value={scopeId} onChange={(event) => setScopeId(event.target.value ? Number(event.target.value) : '')} />
        </Col>
        <Col md={4}>
          <Button onClick={addScope} disabled={scopeId === ''}>
            Add scope
          </Button>
        </Col>
      </Row>
      {scopes.length > 0 && (
        <div className="my-2 d-flex gap-2 flex-wrap">
          {scopes.map((scope) => (
            <Button
              key={`${scope.type}:${scope.id}`}
              size="sm"
              variant="outline-secondary"
              onClick={() => {
                setScopes(scopes.filter((row) => row !== scope))
                setPreview(undefined)
              }}
            >
              {SCOPE_LABELS[scope.type]} #{scope.id} ×
            </Button>
          ))}
        </div>
      )}
      <Button variant="outline-primary" disabled={scopes.length === 0 || previewMutation.isPending} onClick={() => previewMutation.mutate()}>
        Review affected stock
      </Button>
      {preview && (
        <Alert variant="info" className="mt-3">
          <strong>{preview.affected_count} plants represented:</strong> {preview.plants.length} identified plants and {preview.cohorts.length} whole cohorts.
        </Alert>
      )}
      <Row className="g-2 mt-1">
        <Col md={4}>
          <Form.Label>Observation type</Form.Label>
          <Form.Select value={observationType} onChange={(event) => setObservationType(Number(event.target.value))}>
            {types
              .filter((row) => row.active)
              .map((row) => (
                <option key={row.pk} value={row.pk}>
                  {row.name}
                </option>
              ))}
          </Form.Select>
        </Col>
        <Col md={3}>
          <Form.Label>Severity</Form.Label>
          <Form.Select value={severity} onChange={(event) => setSeverity(event.target.value as HealthSeverity)}>
            {SEVERITIES.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </Form.Select>
        </Col>
        <Col md={5}>
          <Form.Label>Follow-up due</Form.Label>
          <Form.Control type="datetime-local" value={followUp} onChange={(event) => setFollowUp(event.target.value)} />
        </Col>
        <Col md={6}>
          <Form.Label>Diagnosis (optional)</Form.Label>
          <Form.Select value={diagnosis} onChange={(event) => setDiagnosis(event.target.value ? Number(event.target.value) : '')}>
            <option value="">Evidence only — no diagnosis</option>
            {diagnoses
              .filter((row) => row.active)
              .map((row) => (
                <option key={row.pk} value={row.pk}>
                  {row.name}
                </option>
              ))}
          </Form.Select>
        </Col>
        <Col md={3}>
          <Form.Label>Certainty</Form.Label>
          <Form.Select disabled={diagnosis === ''} value={certainty} onChange={(event) => setCertainty(event.target.value as 'suspected' | 'confirmed')}>
            <option value="suspected">Suspected</option>
            <option value="confirmed">Confirmed</option>
          </Form.Select>
        </Col>
        <Col md={3}>
          <Form.Label>Evidence URL</Form.Label>
          <Form.Control type="url" value={evidenceUrl} onChange={(event) => setEvidenceUrl(event.target.value)} />
        </Col>
        <Col xs={12}>
          <Form.Label>Notes</Form.Label>
          <Form.Control as="textarea" value={notes} onChange={(event) => setNotes(event.target.value)} />
        </Col>
        <Col xs={12}>
          <PhotoInput id="health-observation-photos" files={photos} onChange={setPhotos} />
        </Col>
      </Row>
      <Button className="mt-3" disabled={!preview || observationType === '' || createMutation.isPending} onClick={() => createMutation.mutate()}>
        Record reviewed observation
      </Button>
      {photoWarning && (
        <Alert className="mt-3" variant="warning">
          {photoWarning}
          {photoTarget !== null && (
            <div>
              <Button
                className="mt-2"
                size="sm"
                variant="outline-dark"
                onClick={() => {
                  void uploadAttachments('health_observation', photoTarget, photos).then((result) => {
                    setPhotos(result.failures.map((failure) => failure.file))
                    setPhotoWarning(result.failures.length === 0 ? '' : photoWarning)
                    if (result.failures.length === 0) setPhotoTarget(null)
                    void invalidateHealth(cache)
                  })
                }}
              >
                Retry failed photos
              </Button>
            </div>
          )}
        </Alert>
      )}
    </Card>
  )
}

function ObservationActions({ observation }: { observation: HealthObservation }) {
  const cache = useQueryClient()
  const { data: locations = [] } = useQuery({ queryKey: queryKeys.locations.list('active'), queryFn: ({ signal }) => getLocations(signal, true) })
  const [reason, setReason] = React.useState('')
  const [destination, setDestination] = React.useState<number | ''>('')
  const [application, setApplication] = React.useState<number | ''>('')
  const [result, setResult] = React.useState('improving')
  const [effectiveness, setEffectiveness] = React.useState('unknown')
  const [correctionReason, setCorrectionReason] = React.useState('')
  const [correctedSeverity, setCorrectedSeverity] = React.useState<HealthSeverity>(observation.severity)
  const [correctedNotes, setCorrectedNotes] = React.useState(observation.notes)
  const [correctionPhotos, setCorrectionPhotos] = React.useState<Array<File>>([])
  const [photoWarning, setPhotoWarning] = React.useState('')
  const [photoTarget, setPhotoTarget] = React.useState<number | null>(null)
  const quarantineMutation = useMutation({
    mutationFn: () =>
      quarantineHealthObservation(observation.pk, {
        idempotency_key: crypto.randomUUID(),
        reason,
        destination: destination === '' ? null : destination
      }),
    onSuccess: () => {
      setReason('')
      void invalidateHealth(cache)
    }
  })
  const treatmentMutation = useMutation({
    mutationFn: () => linkHealthTreatment(observation.pk, { application, notes: reason }),
    onSuccess: () => {
      setApplication('')
      setReason('')
      void invalidateHealth(cache)
    }
  })
  const followUpMutation = useMutation({
    mutationFn: () => recordHealthFollowUp(observation.pk, { result, effectiveness, notes: reason }),
    onSuccess: () => {
      setReason('')
      void invalidateHealth(cache)
    }
  })
  const correctionMutation = useMutation({
    mutationFn: async () => {
      const replacement = await correctHealthObservation(observation.pk, {
        observation_type: observation.observation_type,
        severity: correctedSeverity,
        diagnoses: observation.diagnoses.map((row) => ({ diagnosis: row.diagnosis, certainty: row.certainty })),
        evidence: observation.evidence,
        occurred_at: observation.occurred_at,
        follow_up_due_at: observation.follow_up_due_at,
        notes: correctedNotes,
        correction_reason: correctionReason
      })
      return { replacement, photoResult: await uploadAttachments('health_observation', replacement.pk, correctionPhotos) }
    },
    onSuccess: ({ replacement, photoResult }) => {
      setCorrectionReason('')
      setCorrectionPhotos(photoResult.failures.map((failure) => failure.file))
      setPhotoWarning(photoResult.failures.length === 0 ? '' : 'The correction was recorded, but some photos failed. Retry them on the replacement observation.')
      setPhotoTarget(photoResult.failures.length === 0 ? null : replacement.pk)
      void invalidateHealth(cache)
    }
  })
  return (
    <details className="mt-2">
      <summary>Actions and corrections</summary>
      <Row className="g-2 mt-1 align-items-end">
        <Col md={5}>
          <Form.Label>Reason / notes</Form.Label>
          <Form.Control value={reason} onChange={(event) => setReason(event.target.value)} />
        </Col>
        <Col md={4}>
          <Form.Label>Quarantine destination (optional)</Form.Label>
          <Form.Select value={destination} onChange={(event) => setDestination(event.target.value ? Number(event.target.value) : '')}>
            <option value="">Keep physical location</option>
            {locations
              .filter((row) => row.location_type === 'quarantine')
              .map((row) => (
                <option key={row.pk} value={row.pk}>
                  {row.name}
                </option>
              ))}
          </Form.Select>
        </Col>
        <Col md={3}>
          <Button variant="warning" disabled={!reason || quarantineMutation.isPending} onClick={() => quarantineMutation.mutate()}>
            Quarantine
          </Button>
        </Col>
        <Col md={4}>
          <Form.Label>Posted treatment application</Form.Label>
          <Form.Control type="number" min={1} value={application} onChange={(event) => setApplication(event.target.value ? Number(event.target.value) : '')} />
        </Col>
        <Col md={3}>
          <Button disabled={application === '' || treatmentMutation.isPending} onClick={() => treatmentMutation.mutate()}>
            Link treatment
          </Button>
        </Col>
        <Col md={2}>
          <Form.Select value={result} onChange={(event) => setResult(event.target.value)}>
            <option value="unresolved">Unresolved</option>
            <option value="improving">Improving</option>
            <option value="resolved">Resolved</option>
            <option value="worsened">Worsened</option>
          </Form.Select>
        </Col>
        <Col md={2}>
          <Form.Select value={effectiveness} onChange={(event) => setEffectiveness(event.target.value)}>
            <option value="unknown">Unknown</option>
            <option value="ineffective">Ineffective</option>
            <option value="partial">Partial</option>
            <option value="effective">Effective</option>
          </Form.Select>
        </Col>
        <Col md={1}>
          <Button variant="outline-primary" disabled={followUpMutation.isPending} onClick={() => followUpMutation.mutate()}>
            Follow up
          </Button>
        </Col>
        <Col md={3}>
          <Form.Label>Corrected severity</Form.Label>
          <Form.Select value={correctedSeverity} onChange={(event) => setCorrectedSeverity(event.target.value as HealthSeverity)}>
            {SEVERITIES.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </Form.Select>
        </Col>
        <Col md={9}>
          <Form.Label>Corrected notes</Form.Label>
          <Form.Control value={correctedNotes} onChange={(event) => setCorrectedNotes(event.target.value)} />
        </Col>
        <Col md={8}>
          <Form.Label>Correction reason</Form.Label>
          <Form.Control value={correctionReason} onChange={(event) => setCorrectionReason(event.target.value)} />
        </Col>
        <Col md={8}>
          <PhotoInput id={`health-correction-photos-${observation.pk}`} files={correctionPhotos} onChange={setCorrectionPhotos} label="Correction photos (optional)" />
        </Col>
        <Col md={4}>
          <Button variant="outline-secondary" disabled={!correctionReason || correctionMutation.isPending} onClick={() => correctionMutation.mutate()}>
            Append correction
          </Button>
        </Col>
        {photoWarning && (
          <Col xs={12}>
            <Alert className="mb-0" variant="warning">
              {photoWarning}
              {photoTarget !== null && (
                <Button
                  className="ms-2"
                  size="sm"
                  variant="outline-dark"
                  onClick={() => {
                    void uploadAttachments('health_observation', photoTarget, correctionPhotos).then((result) => {
                      setCorrectionPhotos(result.failures.map((failure) => failure.file))
                      setPhotoWarning(result.failures.length === 0 ? '' : photoWarning)
                      if (result.failures.length === 0) setPhotoTarget(null)
                      void invalidateHealth(cache)
                    })
                  }}
                >
                  Retry failed photos
                </Button>
              )}
            </Alert>
          </Col>
        )}
      </Row>
    </details>
  )
}

function QuarantineRow({ quarantine }: { quarantine: QuarantineCase }) {
  const cache = useQueryClient()
  const [reason, setReason] = React.useState('')
  const mutation = useMutation({
    mutationFn: (action: 'release' | 'escalate' | 'cull') => actOnQuarantine(quarantine.pk, action, { idempotency_key: crypto.randomUUID(), reason }),
    onSuccess: () => {
      setReason('')
      void invalidateHealth(cache)
    }
  })
  return (
    <tr>
      <td>#{quarantine.pk}</td>
      <td>Observation #{quarantine.observation}</td>
      <td>{quarantine.members.reduce((sum, row) => sum + row.quantity, 0)}</td>
      <td>{quarantine.reason}</td>
      <td>
        <Form.Control size="sm" placeholder="Action reason" value={reason} onChange={(event) => setReason(event.target.value)} />
      </td>
      <td className="d-flex gap-1">
        <Button size="sm" variant="success" disabled={!reason || mutation.isPending} onClick={() => mutation.mutate('release')}>
          Release
        </Button>
        <Button size="sm" variant="warning" disabled={!reason || mutation.isPending} onClick={() => mutation.mutate('escalate')}>
          Escalate
        </Button>
        <Button size="sm" variant="danger" disabled={!reason || mutation.isPending} onClick={() => mutation.mutate('cull')}>
          Cull
        </Button>
      </td>
    </tr>
  )
}

function HealthView() {
  const [searchParams] = useSearchParams()
  const initialScopes = React.useMemo(
    () =>
      searchParams.getAll('scope').flatMap((value) => {
        const [type, rawId] = value.split(':')
        const id = Number(rawId)
        if (!(type in SCOPE_LABELS) || !Number.isInteger(id) || id <= 0) return []
        return [{ type: type as HealthScopeType, id }]
      }),
    [searchParams]
  )
  const { data: observations = [], isPending } = useQuery({ queryKey: queryKeys.health.observations, queryFn: ({ signal }) => getHealthObservations(signal) })
  const { data: quarantines = [] } = useQuery({ queryKey: queryKeys.health.quarantines, queryFn: ({ signal }) => getQuarantineCases(signal) })
  const { data: report } = useQuery({ queryKey: queryKeys.health.report, queryFn: ({ signal }) => getHealthReport(signal) })
  const activeCases = quarantines.filter((row) => row.active)
  return (
    <main className="container py-3">
      <h1>Plant health</h1>
      <p>Inspect nursery stock, preserve evidence, constrain availability, connect treatment inputs, and review outcomes.</p>
      {report && (
        <Row className="g-2 mb-3">
          <Col md={3}>
            <Card body>
              <div className="text-muted small">Observations</div>
              <div className="fs-4">{report.summary.observations}</div>
            </Card>
          </Col>
          <Col md={3}>
            <Card body>
              <div className="text-muted small">Active quarantines</div>
              <div className="fs-4 text-warning">{activeCases.length}</div>
            </Card>
          </Col>
          <Col md={6}>
            <Card body>
              <div className="text-muted small">Issues</div>
              {Object.entries(report.summary.by_issue).map(([label, count]) => (
                <Badge bg="secondary" className="me-1" key={label}>
                  {label}: {count}
                </Badge>
              ))}
            </Card>
          </Col>
        </Row>
      )}
      <ObservationComposer initialScopes={initialScopes} />
      <h2 className="h4">Active quarantine</h2>
      {activeCases.length === 0 ? (
        <p className="text-muted">No stock is currently quarantined.</p>
      ) : (
        <Table responsive size="sm">
          <thead>
            <tr>
              <th>Case</th>
              <th>Source</th>
              <th>Plants</th>
              <th>Reason</th>
              <th>Action reason</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {activeCases.map((row) => (
              <QuarantineRow key={row.pk} quarantine={row} />
            ))}
          </tbody>
        </Table>
      )}
      <h2 className="h4 mt-4">Observation history</h2>
      {isPending ? (
        <p>Loading health history…</p>
      ) : (
        observations.map((observation) => (
          <Card body className="mb-2" key={observation.pk}>
            <div className="d-flex justify-content-between">
              <strong>
                #{observation.pk} · {observation.observation_type_name}
              </strong>
              <Badge bg={observation.severity === 'critical' ? 'danger' : observation.severity === 'high' ? 'warning' : 'secondary'}>{observation.severity}</Badge>
            </div>
            <div className="text-muted small">
              {formatDateTime(observation.occurred_at)} · {observation.affected_count} plants represented
            </div>
            <div>{observation.notes || 'No notes.'}</div>
            {observation.diagnoses.map((row) => (
              <Badge className="me-1" bg="info" key={row.diagnosis}>
                {row.certainty}: {row.name}
              </Badge>
            ))}
            {observation.evidence.map((row) => (
              <div key={row.url}>
                <a href={row.url} target="_blank" rel="noreferrer">
                  {row.label || row.url}
                </a>
              </div>
            ))}
            <AttachmentGallery attachments={observation.attachments} />
            <ObservationActions observation={observation} />
          </Card>
        ))
      )}
    </main>
  )
}

export { HealthView }
