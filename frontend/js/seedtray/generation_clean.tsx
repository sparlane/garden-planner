import React from 'react'
import { Alert, Button, Card, Form, Table } from 'react-bootstrap'

import {
  CleanMediaDisposition,
  CleanPlantDisposition,
  CleanPlantOutcome,
  CleanSeedDisposition,
  MediaDispositionChoice,
  SeedDispositionChoice,
  SeedTrayGenerationContents
} from '../types/seedtrays'
import { formatDateTime, formatMeasure, formatMoney } from '../utils'

const PLANT_OUTCOMES: Array<{ value: CleanPlantOutcome; label: string }> = [
  { value: 'failed', label: 'Failed' },
  { value: 'culled', label: 'Culled' },
  { value: 'retained', label: 'Retained' },
  { value: 'donated', label: 'Donated' }
]

const SEED_DISPOSITIONS: Array<{ value: SeedDispositionChoice; label: string }> = [
  { value: 'removed', label: 'Removed and not kept' },
  { value: 'returned', label: 'Returned to the packet' }
]

const MEDIA_DISPOSITIONS: Array<{ value: MediaDispositionChoice; label: string }> = [
  { value: 'waste', label: 'Discarded as waste' },
  { value: 'reclaimed', label: 'Reclaimed into stock' }
]

type PlantChoice = { outcome: CleanPlantOutcome; reason: string }
type SeedChoice = { disposition: SeedDispositionChoice; reason: string }
type MediaChoice = { disposition: MediaDispositionChoice; reason: string; destination: number | '' }

type CleanDecisions = {
  plants: Record<number, PlantChoice>
  seeds: Record<number, SeedChoice>
  media: Record<number, MediaChoice>
}

// Every item starts with a disposition selected, but never a silent one: the
// operator has to read the row and can change it, and the server refuses the
// clean outright if any row is missing. Defaulting to nothing would only turn a
// required decision into a form error.
function initialDecisions(contents: SeedTrayGenerationContents): CleanDecisions {
  return {
    plants: Object.fromEntries(contents.plants.map((plant) => [plant.pk, { outcome: 'failed' as CleanPlantOutcome, reason: '' }])),
    seeds: Object.fromEntries(contents.seeds.map((seed) => [seed.sowing, { disposition: 'removed' as SeedDispositionChoice, reason: '' }])),
    media: Object.fromEntries(contents.media.map((media) => [media.lot, { disposition: 'waste' as MediaDispositionChoice, reason: '', destination: '' as const }]))
  }
}

type LocationOption = { pk: number; name: string }

type GenerationCleanFormProps = {
  contents: SeedTrayGenerationContents
  locations: Array<LocationOption>
  busy: boolean
  onCancel: () => void
  onConfirm: (request: { reason: string; plants: Array<CleanPlantDisposition>; seeds: Array<CleanSeedDisposition>; media: Array<CleanMediaDisposition>; openNext: boolean }) => void
}

const GenerationCleanForm: React.FC<GenerationCleanFormProps> = ({ contents, locations, busy, onCancel, onConfirm }) => {
  const [reason, setReason] = React.useState('')
  const [openNext, setOpenNext] = React.useState(false)
  const [decisions, setDecisions] = React.useState<CleanDecisions>(() => initialDecisions(contents))

  // The digest pins this form to the contents it was built from, so rebuilding
  // it when they change is the same act as taking a fresh digest.
  React.useEffect(() => {
    setDecisions(initialDecisions(contents))
  }, [contents])

  function setPlant(plantPk: number, change: Partial<PlantChoice>) {
    setDecisions((current) => ({ ...current, plants: { ...current.plants, [plantPk]: { ...current.plants[plantPk], ...change } } }))
  }

  function setSeed(sowingPk: number, change: Partial<SeedChoice>) {
    setDecisions((current) => ({ ...current, seeds: { ...current.seeds, [sowingPk]: { ...current.seeds[sowingPk], ...change } } }))
  }

  function setMedia(lotPk: number, change: Partial<MediaChoice>) {
    setDecisions((current) => ({ ...current, media: { ...current.media, [lotPk]: { ...current.media[lotPk], ...change } } }))
  }

  const recoveringWithoutDestination = contents.media.some((media) => {
    const choice = decisions.media[media.lot]
    return choice?.disposition === 'reclaimed' && choice.destination === ''
  })

  function handleConfirm() {
    onConfirm({
      reason,
      openNext,
      plants: contents.plants.map((plant) => ({ plant: plant.pk, ...decisions.plants[plant.pk] })),
      seeds: contents.seeds.map((seed) => ({
        sowing: seed.sowing,
        quantity: String(seed.quantity),
        ...decisions.seeds[seed.sowing]
      })),
      media: contents.media.map((media) => {
        const choice = decisions.media[media.lot]
        return {
          lot: media.lot,
          quantity: media.base_quantity,
          disposition: choice.disposition,
          reason: choice.reason,
          destination: choice.destination === '' ? null : choice.destination
        }
      })
    })
  }

  const nothingLeft = contents.plants.length === 0 && contents.seeds.length === 0 && contents.media.length === 0

  return (
    <Card className="mb-3 border-warning">
      <Card.Body>
        <Card.Title>Clean {contents.code}</Card.Title>
        <p className="text-muted">
          Emptying the tray closes this fill. Nothing is deleted — its sowings, plants, and applications stay readable through the tray history — but every item below needs an
          explicit disposition first.
        </p>
        {nothingLeft && <Alert variant="info">Nothing is left in this fill. Cleaning it will close it and leave the tray empty.</Alert>}

        {contents.plants.length > 0 && (
          <>
            <h6>Plants still in the tray ({contents.plants.length})</h6>
            <Table size="sm" responsive>
              <thead>
                <tr>
                  <th>Plant</th>
                  <th>Germinated</th>
                  <th>Outcome</th>
                  <th>Reason</th>
                </tr>
              </thead>
              <tbody>
                {contents.plants.map((plant) => (
                  <tr key={plant.pk}>
                    <td>#{plant.pk}</td>
                    <td>{formatDateTime(plant.germinated)}</td>
                    <td>
                      <Form.Select
                        size="sm"
                        value={decisions.plants[plant.pk]?.outcome ?? 'failed'}
                        onChange={(event) => setPlant(plant.pk, { outcome: event.target.value as CleanPlantOutcome })}
                      >
                        {PLANT_OUTCOMES.map((option) => (
                          <option key={option.value} value={option.value}>
                            {option.label}
                          </option>
                        ))}
                      </Form.Select>
                    </td>
                    <td>
                      <Form.Control
                        size="sm"
                        value={decisions.plants[plant.pk]?.reason ?? ''}
                        onChange={(event) => setPlant(plant.pk, { reason: event.target.value })}
                        placeholder="Optional"
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </Table>
          </>
        )}

        {contents.seeds.length > 0 && (
          <>
            <h6>Seed drawn but never sown</h6>
            <Table size="sm" responsive>
              <thead>
                <tr>
                  <th>Sowing</th>
                  <th>Seeds left</th>
                  <th>Disposition</th>
                  <th>Reason</th>
                </tr>
              </thead>
              <tbody>
                {contents.seeds.map((seed) => (
                  <tr key={seed.sowing}>
                    <td>#{seed.sowing}</td>
                    <td>{seed.quantity}</td>
                    <td>
                      <Form.Select
                        size="sm"
                        value={decisions.seeds[seed.sowing]?.disposition ?? 'removed'}
                        onChange={(event) => setSeed(seed.sowing, { disposition: event.target.value as SeedDispositionChoice })}
                      >
                        {SEED_DISPOSITIONS.map((option) => (
                          <option key={option.value} value={option.value}>
                            {option.label}
                          </option>
                        ))}
                      </Form.Select>
                    </td>
                    <td>
                      <Form.Control
                        size="sm"
                        value={decisions.seeds[seed.sowing]?.reason ?? ''}
                        onChange={(event) => setSeed(seed.sowing, { reason: event.target.value })}
                        placeholder="Optional"
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </Table>
          </>
        )}

        {contents.media.length > 0 && (
          <>
            <h6>Media applied to this fill</h6>
            <Table size="sm" responsive>
              <thead>
                <tr>
                  <th>Lot</th>
                  <th>Applied</th>
                  <th>Disposition</th>
                  <th>Back into</th>
                  <th>Reason</th>
                </tr>
              </thead>
              <tbody>
                {contents.media.map((media) => {
                  const choice = decisions.media[media.lot]
                  return (
                    <tr key={media.lot}>
                      <td>#{media.lot}</td>
                      <td>{formatMeasure(media.base_quantity, media.base_unit)}</td>
                      <td>
                        <Form.Select
                          size="sm"
                          value={choice?.disposition ?? 'waste'}
                          onChange={(event) => setMedia(media.lot, { disposition: event.target.value as MediaDispositionChoice })}
                        >
                          {MEDIA_DISPOSITIONS.map((option) => (
                            <option key={option.value} value={option.value}>
                              {option.label}
                            </option>
                          ))}
                        </Form.Select>
                      </td>
                      <td>
                        {choice?.disposition === 'reclaimed' ? (
                          <Form.Select
                            size="sm"
                            value={choice.destination}
                            onChange={(event) => setMedia(media.lot, { destination: event.target.value ? Number(event.target.value) : '' })}
                          >
                            <option value="">Select location</option>
                            {locations.map((location) => (
                              <option key={location.pk} value={location.pk}>
                                {location.name}
                              </option>
                            ))}
                          </Form.Select>
                        ) : (
                          <span className="text-muted">—</span>
                        )}
                      </td>
                      <td>
                        <Form.Control size="sm" value={choice?.reason ?? ''} onChange={(event) => setMedia(media.lot, { reason: event.target.value })} placeholder="Optional" />
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </Table>
            <p className="text-muted small">
              Discarded media moves no stock: the application already consumed it, and recording it again would report twice what was used. Reclaimed media goes back to the lot it
              came from.
            </p>
          </>
        )}

        <Form.Group className="mt-3">
          <Form.Label>Why is this fill being cleaned?</Form.Label>
          <Form.Control value={reason} onChange={(event) => setReason(event.target.value)} placeholder="Required" />
        </Form.Group>
        <Form.Check
          className="mt-2"
          type="checkbox"
          id="clean-open-next"
          label="Fill the tray again straight away"
          checked={openNext}
          onChange={(event) => setOpenNext(event.target.checked)}
        />
        <div className="mt-3 d-flex gap-2">
          <Button variant="warning" onClick={handleConfirm} disabled={busy || !reason.trim() || recoveringWithoutDestination}>
            Clean the tray
          </Button>
          <Button variant="secondary" onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
        </div>
        {recoveringWithoutDestination && (
          <Alert variant="warning" className="mt-2 mb-0">
            Say where the reclaimed media was put.
          </Alert>
        )}
      </Card.Body>
    </Card>
  )
}

type GenerationCostPanelProps = {
  breakdown: {
    currency_code: string
    unknown_cost: boolean
    applied_cost: string
    recovered_cost: string
    wasted_cost: string
    allocated_cost: string
    unallocated_cost: string
    production_loss: string
    plants: Array<{ plant: number; cost: string | null }>
  }
}

const GenerationCostPanel: React.FC<GenerationCostPanelProps> = ({ breakdown }) => (
  <>
    {breakdown.unknown_cost && <Alert variant="warning">Some media came from a lot with no recorded unit cost, so these totals understate the real figure.</Alert>}
    <dl className="row mb-2">
      <dt className="col-sm-4">Media applied</dt>
      <dd className="col-sm-8">{formatMoney(breakdown.applied_cost, breakdown.currency_code)}</dd>
      <dt className="col-sm-4">Reclaimed into stock</dt>
      <dd className="col-sm-8">{formatMoney(breakdown.recovered_cost, breakdown.currency_code)}</dd>
      <dt className="col-sm-4">Reaching seedlings</dt>
      <dd className="col-sm-8">{formatMoney(breakdown.allocated_cost, breakdown.currency_code)}</dd>
      <dt className="col-sm-4">In cells with no plant yet</dt>
      <dd className="col-sm-8">{formatMoney(breakdown.unallocated_cost, breakdown.currency_code)}</dd>
      <dt className="col-sm-4">Production loss</dt>
      <dd className="col-sm-8">{formatMoney(breakdown.production_loss, breakdown.currency_code)}</dd>
    </dl>
    {breakdown.plants.length > 0 && (
      <Table size="sm" responsive>
        <thead>
          <tr>
            <th>Plant</th>
            <th>Media cost</th>
          </tr>
        </thead>
        <tbody>
          {breakdown.plants.map((row) => (
            <tr key={row.plant}>
              <td>#{row.plant}</td>
              <td>{formatMoney(row.cost, breakdown.currency_code, 'Unknown')}</td>
            </tr>
          ))}
        </tbody>
      </Table>
    )}
  </>
)

export { GenerationCleanForm, GenerationCostPanel }
