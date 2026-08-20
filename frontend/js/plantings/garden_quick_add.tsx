import React, { useMemo, useState } from 'react'
import { Accordion, Alert, Button, Form, Modal, Table } from 'react-bootstrap'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { createGardenQuickAdd, getGardenQuickAddedPlantings, getProductionBatches, previewGardenQuickAdd } from '../api/plantings'
import { getGardenSquares } from '../api/garden'
import { getLocations } from '../api/locations'
import { getPlants, getPlantVarieties } from '../api/plants'
import { getSeedPackets, getSeeds } from '../api/seeds'
import { getSuppliers } from '../api/supplies'
import { GardenQuickAddEntry, GardenQuickAddReview, GardenPlantingSource, GardenPlantingTracking } from '../types/plantings'
import { queryKeys } from '../query'

const SOURCES: Array<{ value: GardenPlantingSource; label: string }> = [
  { value: 'direct_seed', label: 'Direct seed' },
  { value: 'indoor_raised_seed', label: 'Indoor-raised seed' },
  { value: 'purchased_plant', label: 'Purchased plant' },
  { value: 'cutting', label: 'Cutting' },
  { value: 'division', label: 'Division' },
  { value: 'bulb_tuber_corm', label: 'Bulb, tuber, or corm' },
  { value: 'bare_root', label: 'Bare root' },
  { value: 'volunteer', label: 'Volunteer' },
  { value: 'existing_unknown', label: 'Existing or unknown' }
]

const AGGREGATE_SOURCES = new Set<GardenPlantingSource>(['direct_seed', 'indoor_raised_seed'])

function today(): string {
  const date = new Date()
  const pad = (part: number) => String(part).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

function initialEntry(square?: number): GardenQuickAddEntry {
  return {
    plant: 0,
    source: 'existing_unknown',
    tracking: 'individual',
    quantity: 1,
    recorded_on: today(),
    date_basis: 'first_observed',
    garden_square: square,
    individual_names: []
  }
}

function compactEntry(entry: GardenQuickAddEntry): GardenQuickAddEntry {
  return Object.fromEntries(Object.entries(entry).filter(([, value]) => value !== undefined && value !== '')) as unknown as GardenQuickAddEntry
}

interface GardenQuickAddModalProps {
  show: boolean
  onClose: () => void
  initialSquare?: number
}

function GardenQuickAddModal({ show, onClose, initialSquare }: GardenQuickAddModalProps) {
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState<GardenQuickAddEntry>(() => initialEntry(initialSquare))
  const [staged, setStaged] = useState<Array<GardenQuickAddEntry>>([])
  const [review, setReview] = useState<GardenQuickAddReview>()
  const [formError, setFormError] = useState<string>()
  const { data: plants = [] } = useQuery({ queryKey: queryKeys.plants.plants, queryFn: ({ signal }) => getPlants(signal) })
  const { data: varieties = [] } = useQuery({ queryKey: queryKeys.plants.varieties, queryFn: ({ signal }) => getPlantVarieties(signal) })
  const { data: squares = [] } = useQuery({ queryKey: queryKeys.garden.squares, queryFn: ({ signal }) => getGardenSquares(signal) })
  const { data: locations = [] } = useQuery({ queryKey: queryKeys.locations.list('active'), queryFn: ({ signal }) => getLocations(signal, true) })
  const { data: batches = [] } = useQuery({
    queryKey: [...queryKeys.plantings.batchesAll, 'quick-add'],
    queryFn: ({ signal }) => getProductionBatches({ status: 'active' }, signal)
  })
  const { data: seedCatalog = [] } = useQuery({ queryKey: queryKeys.seeds.catalog, queryFn: ({ signal }) => getSeeds(signal) })
  const { data: seedPackets = [] } = useQuery({ queryKey: queryKeys.seeds.packets.raw, queryFn: ({ signal }) => getSeedPackets(signal) })
  const { data: suppliers = [] } = useQuery({ queryKey: queryKeys.suppliers.all, queryFn: ({ signal }) => getSuppliers(signal) })
  const matchingVarieties = varieties.filter((variety) => variety.plant === draft.plant)
  const matchingBatches = batches.filter((batch) => batch.variety === draft.variety)
  const matchingPackets = useMemo(() => {
    const seedIds = new Set(seedCatalog.filter((seed) => seed.plant_variety === draft.variety).map((seed) => seed.pk))
    return seedPackets.filter((packet) => seedIds.has(packet.seeds) && packet.empty !== true)
  }, [draft.variety, seedCatalog, seedPackets])

  const previewMutation = useMutation({
    mutationFn: previewGardenQuickAdd,
    onSuccess: setReview
  })
  const createMutation = useMutation({
    mutationFn: createGardenQuickAdd,
    onSuccess: async () => {
      await Promise.all([queryClient.invalidateQueries({ queryKey: queryKeys.plantings.all }), queryClient.invalidateQueries({ queryKey: queryKeys.seeds.packets.all })])
      closeAndReset()
    }
  })

  function update<K extends keyof GardenQuickAddEntry>(field: K, value: GardenQuickAddEntry[K]) {
    setDraft((current) => ({ ...current, [field]: value }))
    setReview(undefined)
  }

  function changeSource(source: GardenPlantingSource) {
    const aggregate = AGGREGATE_SOURCES.has(source)
    setDraft((current) => ({
      ...current,
      source,
      tracking: aggregate ? 'aggregate' : 'individual',
      date_basis: source === 'volunteer' || source === 'existing_unknown' ? 'first_observed' : 'planted',
      quantity_is_approximate: aggregate ? current.quantity_is_approximate : false,
      individual_names: aggregate ? [] : current.individual_names,
      seed_packet: aggregate ? current.seed_packet : undefined,
      seed_quantity_used: aggregate ? current.seed_quantity_used : undefined
    }))
    setReview(undefined)
  }

  function changePlace(value: string) {
    const [kind, rawPk] = value.split(':')
    const pk = rawPk ? Number(rawPk) : undefined
    setDraft((current) => ({ ...current, garden_square: kind === 'square' ? pk : undefined, location: kind === 'location' ? pk : undefined }))
    setReview(undefined)
  }

  function validateDraft(): string | undefined {
    if (!draft.plant) return 'Choose a crop.'
    if (!draft.variety && !draft.new_variety_name?.trim()) return 'Choose a variety or enter a new variety name.'
    if (!draft.garden_square && !draft.location) return 'Choose a location.'
    if (draft.quantity < 1) return 'Quantity must be at least one.'
    if (!draft.recorded_on) return 'Choose a planted or first-observed date.'
    return undefined
  }

  function stageDraft() {
    const error = validateDraft()
    if (error) {
      setFormError(error)
      return
    }
    setStaged((current) => [...current, compactEntry(draft)])
    setDraft(initialEntry(initialSquare))
    setReview(undefined)
    setFormError(undefined)
  }

  function reviewEntries() {
    const error = validateDraft()
    const entries = staged.length > 0 && error ? staged : [...staged, compactEntry(draft)]
    if (entries.length === 0 || (staged.length === 0 && error)) {
      setFormError(error ?? 'Add a planting.')
      return
    }
    setFormError(undefined)
    previewMutation.mutate(entries)
  }

  function closeAndReset() {
    setDraft(initialEntry(initialSquare))
    setStaged([])
    setReview(undefined)
    setFormError(undefined)
    onClose()
  }

  const placeValue = draft.garden_square ? `square:${draft.garden_square}` : draft.location ? `location:${draft.location}` : ''
  const selectedPlantName = (entry: GardenQuickAddEntry) => plants.find((plant) => plant.pk === entry.plant)?.name ?? `Crop ${entry.plant}`
  const selectedVarietyName = (entry: GardenQuickAddEntry) => entry.new_variety_name ?? varieties.find((variety) => variety.pk === entry.variety)?.name ?? ''

  return (
    <Modal show={show} onHide={closeAndReset} size="lg" aria-labelledby="garden-quick-add-title">
      <Modal.Header closeButton>
        <Modal.Title id="garden-quick-add-title">Add what is growing</Modal.Title>
      </Modal.Header>
      <Modal.Body>
        {formError && <Alert variant="danger">{formError}</Alert>}
        <div className="row g-3">
          <Form.Group className="col-md-6" controlId="quick-add-crop">
            <Form.Label>Crop</Form.Label>
            <Form.Select
              value={draft.plant || ''}
              onChange={(event) =>
                setDraft({
                  ...draft,
                  plant: Number(event.target.value),
                  variety: undefined,
                  new_variety_name: undefined,
                  batch: undefined,
                  seed_packet: undefined,
                  seed_quantity_used: undefined
                })
              }
            >
              <option value="">Choose a crop</option>
              {plants.map((plant) => (
                <option key={plant.pk} value={plant.pk}>
                  {plant.name}
                </option>
              ))}
            </Form.Select>
          </Form.Group>
          <Form.Group className="col-md-6" controlId="quick-add-variety">
            <Form.Label>Existing variety</Form.Label>
            <Form.Select
              value={draft.variety ?? ''}
              onChange={(event) =>
                setDraft({
                  ...draft,
                  variety: event.target.value ? Number(event.target.value) : undefined,
                  new_variety_name: undefined,
                  batch: undefined,
                  seed_packet: undefined,
                  seed_quantity_used: undefined
                })
              }
            >
              <option value="">Enter a new variety below</option>
              {matchingVarieties.map((variety) => (
                <option key={variety.pk} value={variety.pk}>
                  {variety.name}
                </option>
              ))}
            </Form.Select>
          </Form.Group>
          <Form.Group className="col-md-6" controlId="quick-add-new-variety">
            <Form.Label>New variety name</Form.Label>
            <Form.Control
              value={draft.new_variety_name ?? ''}
              onChange={(event) =>
                setDraft({
                  ...draft,
                  new_variety_name: event.target.value || undefined,
                  variety: undefined,
                  batch: undefined,
                  seed_packet: undefined,
                  seed_quantity_used: undefined
                })
              }
              placeholder="Required when no existing variety is selected"
            />
          </Form.Group>
          <Form.Group className="col-md-6" controlId="quick-add-source">
            <Form.Label>How it started</Form.Label>
            <Form.Select value={draft.source} onChange={(event) => changeSource(event.target.value as GardenPlantingSource)}>
              {SOURCES.map((source) => (
                <option key={source.value} value={source.value}>
                  {source.label}
                </option>
              ))}
            </Form.Select>
          </Form.Group>
          <Form.Group className="col-md-6" controlId="quick-add-place">
            <Form.Label>Location</Form.Label>
            <Form.Select value={placeValue} onChange={(event) => changePlace(event.target.value)}>
              <option value="">Choose a location</option>
              <optgroup label="Garden squares">
                {squares.map((square) => (
                  <option key={`s-${square.pk}`} value={`square:${square.pk}`}>
                    {square.name}
                  </option>
                ))}
              </optgroup>
              <optgroup label="Other places">
                {locations.map((location) => (
                  <option key={`l-${location.pk}`} value={`location:${location.pk}`}>
                    {location.full_name}
                  </option>
                ))}
              </optgroup>
            </Form.Select>
          </Form.Group>
          <Form.Group className="col-md-3" controlId="quick-add-quantity">
            <Form.Label>Approximate quantity</Form.Label>
            <Form.Control type="number" min={1} value={draft.quantity} onChange={(event) => update('quantity', Number(event.target.value))} />
            <Form.Check
              className="mt-1"
              label="This count is approximate"
              checked={draft.quantity_is_approximate ?? false}
              disabled={draft.tracking === 'individual'}
              onChange={(event) => update('quantity_is_approximate', event.target.checked)}
            />
          </Form.Group>
          <Form.Group className="col-md-3" controlId="quick-add-tracking">
            <Form.Label>Track as</Form.Label>
            <Form.Select
              value={draft.tracking}
              onChange={(event) => {
                const tracking = event.target.value as GardenPlantingTracking
                setDraft((current) => ({
                  ...current,
                  tracking,
                  quantity_is_approximate: tracking === 'individual' ? false : current.quantity_is_approximate,
                  individual_names: tracking === 'aggregate' ? [] : current.individual_names
                }))
                setReview(undefined)
              }}
            >
              <option value="aggregate">One crop or patch</option>
              <option value="individual">Individual plants</option>
            </Form.Select>
          </Form.Group>
          <Form.Group className="col-md-3" controlId="quick-add-date">
            <Form.Label>Date means</Form.Label>
            <Form.Select className="mb-2" value={draft.date_basis} onChange={(event) => update('date_basis', event.target.value as 'planted' | 'first_observed')}>
              <option value="planted">Planted</option>
              <option value="first_observed">First observed</option>
            </Form.Select>
            <Form.Control type="date" value={draft.recorded_on} onChange={(event) => update('recorded_on', event.target.value)} />
            <Form.Check
              className="mt-1"
              label="Date is approximate"
              checked={draft.date_is_approximate ?? false}
              onChange={(event) => update('date_is_approximate', event.target.checked)}
            />
          </Form.Group>
          <div className="col-md-3 d-flex align-items-end pb-2">
            <Form.Check label="Perennial planting" checked={draft.perennial ?? false} onChange={(event) => update('perennial', event.target.checked)} />
          </div>
          {draft.tracking === 'individual' && (
            <Form.Group className="col-12" controlId="quick-add-names">
              <Form.Label>Plant names (optional, one per line)</Form.Label>
              <Form.Control
                as="textarea"
                rows={2}
                value={(draft.individual_names ?? []).join('\n')}
                onChange={(event) => update('individual_names', event.target.value.split('\n'))}
              />
            </Form.Group>
          )}
          <Form.Group className="col-12" controlId="quick-add-notes">
            <Form.Label>Notes</Form.Label>
            <Form.Control as="textarea" rows={2} value={draft.notes ?? ''} onChange={(event) => update('notes', event.target.value)} />
          </Form.Group>
        </div>
        <Accordion className="mt-3">
          <Accordion.Item eventKey="advanced">
            <Accordion.Header>Advanced source and cost details</Accordion.Header>
            <Accordion.Body>
              <div className="row g-3">
                <Form.Group className="col-md-6" controlId="quick-add-batch">
                  <Form.Label>Existing planting cycle</Form.Label>
                  <Form.Select
                    value={draft.batch ?? ''}
                    disabled={!draft.variety}
                    onChange={(event) => update('batch', event.target.value ? Number(event.target.value) : undefined)}
                  >
                    <option value="">Create a new cycle</option>
                    {matchingBatches.map((batch) => (
                      <option key={batch.pk} value={batch.pk}>
                        {batch.code}
                      </option>
                    ))}
                  </Form.Select>
                </Form.Group>
                <Form.Group className="col-md-6" controlId="quick-add-packet">
                  <Form.Label>Seed packet</Form.Label>
                  <Form.Select
                    value={draft.seed_packet ?? ''}
                    disabled={!AGGREGATE_SOURCES.has(draft.source) || !draft.variety}
                    onChange={(event) => update('seed_packet', event.target.value ? Number(event.target.value) : undefined)}
                  >
                    <option value="">No packet attribution</option>
                    {matchingPackets.map((packet) => (
                      <option key={packet.pk} value={packet.pk}>
                        Packet {packet.pk}
                      </option>
                    ))}
                  </Form.Select>
                </Form.Group>
                {draft.seed_packet ? (
                  <Form.Group className="col-md-6" controlId="quick-add-seed-quantity">
                    <Form.Label>Exact seeds or clusters used</Form.Label>
                    <Form.Control
                      type="number"
                      min="0.000000001"
                      step="any"
                      value={draft.seed_quantity_used ?? ''}
                      onChange={(event) => update('seed_quantity_used', event.target.value)}
                    />
                  </Form.Group>
                ) : (
                  <>
                    <Form.Group className="col-md-6" controlId="quick-add-supplier">
                      <Form.Label>Supplier</Form.Label>
                      <Form.Select value={draft.supplier ?? ''} onChange={(event) => update('supplier', event.target.value ? Number(event.target.value) : undefined)}>
                        <option value="">Unknown or not applicable</option>
                        {suppliers.map((supplier) => (
                          <option key={supplier.pk} value={supplier.pk}>
                            {supplier.name}
                          </option>
                        ))}
                      </Form.Select>
                    </Form.Group>
                    <Form.Group className="col-md-6" controlId="quick-add-cost">
                      <Form.Label>Total purchase cost</Form.Label>
                      <Form.Control type="number" min="0" step="0.01" value={draft.purchase_cost ?? ''} onChange={(event) => update('purchase_cost', event.target.value)} />
                    </Form.Group>
                  </>
                )}
                {draft.location && (
                  <Form.Group className="col-12" controlId="quick-add-override">
                    <Form.Label>Capacity override reason (only if needed)</Form.Label>
                    <Form.Control value={draft.override_reason ?? ''} onChange={(event) => update('override_reason', event.target.value)} />
                  </Form.Group>
                )}
              </div>
            </Accordion.Body>
          </Accordion.Item>
        </Accordion>
        <div className="d-flex gap-2 mt-3">
          <Button variant="outline-primary" onClick={stageDraft}>
            Add another to review
          </Button>
          <Button onClick={reviewEntries} disabled={previewMutation.isPending}>
            Review {staged.length > 0 ? `${staged.length}+ plantings` : 'planting'}
          </Button>
        </div>
        {staged.length > 0 && (
          <Table responsive size="sm" className="mt-3">
            <thead>
              <tr>
                <th>Crop</th>
                <th>Variety</th>
                <th>Source</th>
                <th>Quantity</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {staged.map((entry, index) => (
                <tr key={`${entry.plant}-${index}`}>
                  <td>{selectedPlantName(entry)}</td>
                  <td>{selectedVarietyName(entry)}</td>
                  <td>{SOURCES.find((source) => source.value === entry.source)?.label}</td>
                  <td>{entry.quantity}</td>
                  <td>
                    <Button
                      size="sm"
                      variant="outline-danger"
                      onClick={() => {
                        setStaged(staged.filter((_entry, row) => row !== index))
                        setReview(undefined)
                      }}
                    >
                      Remove
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
        {review && (
          <Alert variant={review.warnings.length ? 'warning' : 'success'} className="mt-3">
            <Alert.Heading>
              Review {review.entries.length} planting{review.entries.length === 1 ? '' : 's'}
            </Alert.Heading>
            {review.warnings.length === 0 ? (
              <p>No duplicate or occupancy warnings.</p>
            ) : (
              <ul>
                {review.warnings.map((warning, index) => (
                  <li key={`${warning.code}-${warning.entry}-${index}`}>
                    Row {warning.entry + 1}: {warning.message}
                  </li>
                ))}
              </ul>
            )}
            <Button onClick={() => createMutation.mutate(review)} disabled={createMutation.isPending}>
              Create all
            </Button>
          </Alert>
        )}
      </Modal.Body>
      <Modal.Footer>
        <Button variant="secondary" onClick={closeAndReset}>
          Cancel
        </Button>
      </Modal.Footer>
    </Modal>
  )
}

function GardenQuickAddButton({ initialSquare, label = 'Add what is growing' }: { initialSquare?: number; label?: string }) {
  const [show, setShow] = useState(false)
  return (
    <>
      <Button onClick={() => setShow(true)}>{label}</Button>
      <GardenQuickAddModal show={show} onClose={() => setShow(false)} initialSquare={initialSquare} />
    </>
  )
}

function GardenQuickAddedList() {
  const { data: entries = [], isPending } = useQuery({
    queryKey: [...queryKeys.plantings.all, 'garden-quick-add'],
    queryFn: ({ signal }) => getGardenQuickAddedPlantings(signal)
  })
  if (isPending) return <p>Loading quick-added plantings…</p>
  if (entries.length === 0) return null
  return (
    <section className="mb-4" aria-labelledby="quick-added-heading">
      <h2 id="quick-added-heading" className="h5">
        Quick-added plantings
      </h2>
      <Table responsive size="sm">
        <thead>
          <tr>
            <th>Crop</th>
            <th>Source</th>
            <th>Tracking</th>
            <th>Quantity</th>
            <th>Date</th>
            <th>Location</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((entry) => (
            <tr key={entry.pk}>
              <td>
                {entry.plant_name} — {entry.variety_name}
                {entry.perennial ? ' · perennial' : ''}
              </td>
              <td>{SOURCES.find((source) => source.value === entry.source)?.label}</td>
              <td>{entry.tracking === 'individual' ? 'Individual plants' : 'Crop or patch'}</td>
              <td>
                {entry.quantity_is_approximate ? 'About ' : ''}
                {entry.quantity}
              </td>
              <td>
                {entry.date_is_approximate ? 'About ' : ''}
                {entry.recorded_on}
              </td>
              <td>{entry.location_label}</td>
            </tr>
          ))}
        </tbody>
      </Table>
    </section>
  )
}

export { GardenQuickAddButton, GardenQuickAddModal, GardenQuickAddedList }
