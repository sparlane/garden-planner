import React from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Col, Form, Row } from 'react-bootstrap'

import { PhotoInput } from '../attachments'
import { uploadAttachments } from '../api/attachments'
import { addHarvest } from '../api/plantings'
import { queryKeys } from '../query'
import { localDatetimeInputValue, parseLocalDatetimeInput } from '../utils'
import { HarvestGrade, HarvestUnitCode } from '../types/plantings'
import { Workspace } from '../types/workspace'

const UNIT_OPTIONS: Array<{ code: HarvestUnitCode; label: string }> = [
  { code: 'each', label: 'Each (count)' },
  { code: 'g', label: 'Grams' },
  { code: 'kg', label: 'Kilograms' },
  { code: 'ml', label: 'Millilitres' },
  { code: 'l', label: 'Litres' }
]

const GRADE_OPTIONS: Array<{ value: HarvestGrade; label: string }> = [
  { value: 'ungraded', label: 'Ungraded' },
  { value: 'premium', label: 'Premium' },
  { value: 'standard', label: 'Standard' },
  { value: 'seconds', label: 'Seconds' }
]

interface HarvestFormBatch {
  pk: number
  label: string
}

interface HarvestFormPlant {
  pk: number
  label: string
  batch: number
  // When this plant arrived where it is now. A final harvest recorded before
  // this instant is refused by the server, because a plant cannot be finished
  // before it reached the place it was finished in.
  since: string | null
}

interface HarvestFormProps {
  batches: Array<HarvestFormBatch>
  plants?: Array<HarvestFormPlant>
  gardenSquare?: number
  gardenRow?: number
  onRecorded?: () => void
  workspace: Workspace
}

function invalidateHarvests(queryClient: ReturnType<typeof useQueryClient>, batchPk: number, finishedPlants: boolean) {
  const families: Array<readonly unknown[]> = [
    queryKeys.plantings.harvestsAll,
    queryKeys.plantings.harvestReportAll,
    queryKeys.plantings.batch(batchPk),
    queryKeys.plantings.batchesAll
  ]
  if (finishedPlants) {
    families.push(queryKeys.plantings.plantLifecycleAll, queryKeys.plantings.specificPlantsAll, queryKeys.plantings.currentSeedTrays, queryKeys.plantings.currentGardenSquares)
  }
  return Promise.all(families.map((queryKey) => queryClient.invalidateQueries({ queryKey })))
}

function HarvestForm({ batches, plants = [], gardenSquare, gardenRow, onRecorded, workspace }: HarvestFormProps) {
  const queryClient = useQueryClient()
  const [batch, setBatch] = React.useState<number | ''>(batches.length === 1 ? batches[0].pk : '')
  const [harvestedAt, setHarvestedAt] = React.useState(localDatetimeInputValue())
  const [quantity, setQuantity] = React.useState('')
  const [unitCode, setUnitCode] = React.useState<HarvestUnitCode>('kg')
  const [qualityRating, setQualityRating] = React.useState('')
  const [grade, setGrade] = React.useState<HarvestGrade>('ungraded')
  const [notes, setNotes] = React.useState('')
  const [selected, setSelected] = React.useState<Array<number>>([])
  const [finishPlants, setFinishPlants] = React.useState(false)
  const [error, setError] = React.useState<string>()
  const [photos, setPhotos] = React.useState<Array<File>>([])
  const [photoWarning, setPhotoWarning] = React.useState('')
  const [photoTarget, setPhotoTarget] = React.useState<number | null>(null)

  React.useEffect(() => {
    if (batches.length === 1) {
      setBatch(batches[0].pk)
    }
  }, [batches])

  const batchPlants = plants.filter((plant) => plant.batch === batch)
  const chosen = batchPlants.filter((plant) => selected.includes(plant.pk))
  const harvestedDate = parseLocalDatetimeInput(harvestedAt)
  // A plant cannot be finished before it arrived where it stands, so warn
  // rather than let the server refuse the whole harvest.
  const predatesAPlant = chosen.some((plant) => plant.since !== null && harvestedDate !== null && harvestedDate < new Date(plant.since))

  const mutation = useMutation({
    mutationFn: async (data: Parameters<typeof addHarvest>[0]) => {
      const harvest = await addHarvest(data)
      return { harvest, photoResult: await uploadAttachments('harvest', harvest.pk, photos) }
    },
    onSuccess: ({ harvest, photoResult }) => {
      setQuantity('')
      setNotes('')
      setSelected([])
      setFinishPlants(false)
      setError(undefined)
      setPhotos(photoResult.failures.map((failure) => failure.file))
      setPhotoWarning(photoResult.failures.length === 0 ? '' : 'The harvest was recorded, but some photos failed. The failed selection is ready to retry.')
      setPhotoTarget(photoResult.failures.length === 0 ? null : harvest.pk)
      return invalidateHarvests(queryClient, harvest.batch, harvest.finished_plants.length > 0).then(() => onRecorded?.())
    },
    onError: (caught: unknown) => setError(caught instanceof Error ? caught.message : String(caught))
  })

  function togglePlant(plantPk: number) {
    setSelected((current) => (current.includes(plantPk) ? current.filter((pk) => pk !== plantPk) : [...current, plantPk]))
  }

  function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (batch === '' || harvestedDate === null) {
      return
    }
    setError(undefined)
    mutation.mutate({
      batch,
      harvested_at: harvestedDate.toISOString(),
      quantity,
      unit_code: unitCode,
      garden_square: gardenSquare ?? null,
      garden_row: gardenRow ?? null,
      quality_rating: qualityRating === '' ? null : Number(qualityRating),
      grade,
      notes,
      plants: selected,
      finish_plants: finishPlants
    })
  }

  if (batches.length === 0) {
    return (
      <p className="text-muted mb-0">
        {workspace.mode === 'garden' ? 'Nothing here has a planting cycle to record a harvest against.' : 'No crop here has a production batch to record a harvest against.'}
      </p>
    )
  }

  return (
    <Form onSubmit={submit}>
      <Row className="g-2">
        <Col md={4}>
          <Form.Group controlId="harvest-batch">
            <Form.Label>Crop</Form.Label>
            <Form.Select value={batch} onChange={(event) => setBatch(event.target.value === '' ? '' : Number(event.target.value))}>
              <option value="">Choose a crop…</option>
              {batches.map((option) => (
                <option key={option.pk} value={option.pk}>
                  {option.label}
                </option>
              ))}
            </Form.Select>
          </Form.Group>
        </Col>
        <Col md={4}>
          <Form.Group controlId="harvest-quantity">
            <Form.Label>Quantity</Form.Label>
            <Form.Control value={quantity} onChange={(event) => setQuantity(event.target.value)} inputMode="decimal" placeholder="e.g. 1.5" required />
          </Form.Group>
        </Col>
        <Col md={4}>
          <Form.Group controlId="harvest-unit">
            <Form.Label>Unit</Form.Label>
            <Form.Select value={unitCode} onChange={(event) => setUnitCode(event.target.value as HarvestUnitCode)}>
              {UNIT_OPTIONS.map((option) => (
                <option key={option.code} value={option.code}>
                  {option.label}
                </option>
              ))}
            </Form.Select>
          </Form.Group>
        </Col>
        <Col md={4}>
          <Form.Group controlId="harvest-at">
            <Form.Label>Harvested</Form.Label>
            <Form.Control type="datetime-local" value={harvestedAt} onChange={(event) => setHarvestedAt(event.target.value)} required />
          </Form.Group>
        </Col>
        <Col md={4}>
          <Form.Group controlId="harvest-quality">
            <Form.Label>Quality</Form.Label>
            <Form.Select value={qualityRating} onChange={(event) => setQualityRating(event.target.value)}>
              <option value="">Not rated</option>
              {[1, 2, 3, 4, 5].map((rating) => (
                <option key={rating} value={rating}>
                  {rating}
                </option>
              ))}
            </Form.Select>
          </Form.Group>
        </Col>
        <Col md={4}>
          <Form.Group controlId="harvest-grade">
            <Form.Label>Grade</Form.Label>
            <Form.Select value={grade} onChange={(event) => setGrade(event.target.value as HarvestGrade)}>
              {GRADE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </Form.Select>
          </Form.Group>
        </Col>
        <Col md={12}>
          <Form.Group controlId="harvest-notes">
            <Form.Label>Notes</Form.Label>
            <Form.Control value={notes} onChange={(event) => setNotes(event.target.value)} />
          </Form.Group>
        </Col>
        <Col md={12}>
          <PhotoInput id="harvest-photos" files={photos} onChange={setPhotos} />
        </Col>
      </Row>
      {batchPlants.length > 0 && (
        <fieldset className="mt-3">
          <legend className="fs-6">Plants this came from (optional)</legend>
          <div className="d-flex flex-wrap gap-3">
            {batchPlants.map((plant) => (
              <Form.Check
                key={plant.pk}
                type="checkbox"
                id={`harvest-plant-${plant.pk}`}
                label={plant.label}
                checked={selected.includes(plant.pk)}
                onChange={() => togglePlant(plant.pk)}
              />
            ))}
          </div>
          <Form.Check
            className="mt-2"
            type="checkbox"
            id="harvest-finish-plants"
            label="This harvest finished the selected plants"
            checked={finishPlants}
            disabled={selected.length === 0 || predatesAPlant}
            onChange={(event) => setFinishPlants(event.target.checked)}
          />
          {predatesAPlant && <Form.Text className="text-warning">This time is before a selected plant reached where it stands, so it cannot also finish that plant.</Form.Text>}
          {selected.length > 0 && !finishPlants && <Form.Text className="text-muted">Recording a harvest leaves these plants growing, so they can be picked again.</Form.Text>}
        </fieldset>
      )}
      <Button className="mt-3" type="submit" disabled={batch === '' || quantity.trim() === '' || mutation.isPending}>
        {mutation.isPending ? 'Recording…' : 'Record harvest'}
      </Button>
      {error && (
        <Alert className="mt-3" variant="danger">
          {error}
        </Alert>
      )}
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
                  void uploadAttachments('harvest', photoTarget, photos).then((result) => {
                    setPhotos(result.failures.map((failure) => failure.file))
                    setPhotoWarning(result.failures.length === 0 ? '' : photoWarning)
                    if (result.failures.length === 0) setPhotoTarget(null)
                    void queryClient.invalidateQueries({ queryKey: queryKeys.plantings.harvestsAll })
                  })
                }}
              >
                Retry failed photos
              </Button>
            </div>
          )}
        </Alert>
      )}
    </Form>
  )
}

export { HarvestForm, HarvestFormBatch, HarvestFormPlant, UNIT_OPTIONS, invalidateHarvests }
