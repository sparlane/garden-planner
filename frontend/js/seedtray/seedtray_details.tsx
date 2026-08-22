// This is an entry point, not a view imported by one, so it has to pull in the
// stylesheet itself: esbuild emits a bundle's CSS only from what that bundle
// imports, and `seedtray_detail.html` links the file this produces.
import 'bootstrap/dist/css/bootstrap.css'

import React from 'react'
import * as ReactDOM from 'react-dom/client'
import { QueryClientProvider, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { CleanMediaDisposition, CleanPlantDisposition, CleanSeedDisposition, SeedTray, SeedTrayCell, SeedTrayGeneration } from '../types/seedtrays'
import { localDatetimeInputValue, parseLocalDatetimeInput, formatDate, formatDateTime } from '../utils'
import {
  cleanSeedTrayGeneration,
  getSeedTrayCells,
  getSeedTrayGenerationContents,
  getSeedTrayGenerationCost,
  getSeedTrayGenerations,
  getSeedTrayModels,
  getSeedTrays,
  getSerializedUnitMovements,
  openSeedTrayGeneration,
  postSerializedUnitAction,
  reopenSeedTrayGeneration,
  reviewSeedTrayGeneration
} from '../api/seedtrays'
import { GenerationCleanForm, GenerationCostPanel } from './generation_clean'
import { buildSeedTrayCellGrid } from './grid'
import { Alert, Button, Card, Form, Table } from 'react-bootstrap'
import { BulkPlantOperationRequest, PlantLifecycleEvent, PlantOutcomeAction, SeedTrayPlanting, SpecificPlant, SpecificPlantLocation, SpecificPlantMove } from '../types/plantings'
import {
  getPlantingSeedTray,
  getSpecificPlantsBySeedTray,
  getSpecificPlantLifecycleEvents,
  postBulkPlantOperation,
  previewBulkPlantOperation,
  moveSpecificPlant,
  postSpecificPlantOutcome,
  reverseSpecificPlantEvent
} from '../api/plantings'
import { PlantLifecycleBadge, PlantLifecycleHistory, PlantOutcomeButtons, REASON_PROMPTS, REASON_REQUIRED_ACTIONS } from '../plantings/lifecycle'
import { ApiErrorAlert } from '../api_error_alert'
import { SeedPacketDetails } from '../types/seeds'
import { getSeedPacketsCurrent } from '../api/seeds'
import { GardenSquare } from '../types/garden'
import { getGardenSquares } from '../api/garden'
import { getLocations } from '../api/locations'
import { queryClient, queryKeys } from '../query'
import { InputApplicationForm } from '../applications/application_form'

interface SeedTrayDetailsProps {
  seedTrayPk: number
}

type BaseMoveForm = {
  plantPk: number
  currentLocationPk?: number
  date: string
  notes: string
}

type GardenSquareMove = {
  locationType: 'garden_square'
  gardenSquarePk?: number
}

type SeedTrayMove = {
  locationType: 'seed_tray_cell'
  moveSeedTrayPk?: number
  seedTrayCellPk?: number
}

type MoveForm = BaseMoveForm & (GardenSquareMove | SeedTrayMove)
type InventoryAction = 'transfer' | 'loss' | 'retire' | 'return' | 'reconcile-opening'

type CellPlantingEntry = { cellPlantingPk: number; quantity: number; plantingPk: number }

type CleanGenerationRequestPayload = {
  reason: string
  digest: string
  plants: Array<CleanPlantDisposition>
  seeds: Array<CleanSeedDisposition>
  media: Array<CleanMediaDisposition>
  open_next: boolean
}

function computeCellData(specificPlants: Array<SpecificPlant> | undefined, plantings: Array<SeedTrayPlanting> | undefined) {
  const { cellCurrentPlantMap, cellPlantingMap, germinatedByCellPlanting } = buildCellMaps(specificPlants, plantings)
  const cellTotals: { [cellPk: number]: number } = {}
  plantings?.forEach((p) => {
    if (p.removed) return
    p.cell_plantings?.forEach((cp) => {
      cellTotals[cp.cell] = (cellTotals[cp.cell] || 0) + cp.quantity
    })
  })
  return { cellCurrentPlantMap, cellPlantingMap, germinatedByCellPlanting, cellTotals }
}

function currentLocation(plant: SpecificPlant): SpecificPlantLocation | undefined {
  return plant.locations.find((l) => !l.ended)
}

type CellMaps = {
  cellCurrentPlantMap: { [cellPk: number]: Array<SpecificPlant> }
  cellPlantingMap: { [cellPk: number]: Array<CellPlantingEntry> }
  germinatedByCellPlanting: { [cellPlantingPk: number]: number }
}

function buildCellMaps(specificPlants: Array<SpecificPlant> | undefined, plantings: Array<SeedTrayPlanting> | undefined): CellMaps {
  const cellCurrentPlantMap: { [cellPk: number]: Array<SpecificPlant> } = {}
  const cellPlantingMap: { [cellPk: number]: Array<CellPlantingEntry> } = {}
  const germinatedByCellPlanting: { [cellPlantingPk: number]: number } = {}

  ;(specificPlants ?? []).forEach((plant) => {
    const loc = currentLocation(plant)
    if (loc?.location_type === 'seed_tray_cell' && loc.seed_tray_cell != null) {
      ;(cellCurrentPlantMap[loc.seed_tray_cell] ??= []).push(plant)
    }
    germinatedByCellPlanting[plant.cell_planting] = (germinatedByCellPlanting[plant.cell_planting] ?? 0) + 1
  })
  ;(plantings ?? []).forEach((p) => {
    if (p.removed) return
    p.cell_plantings?.forEach((cp) => {
      ;(cellPlantingMap[cp.cell] ??= []).push({ cellPlantingPk: cp.pk, quantity: cp.quantity, plantingPk: p.pk })
    })
  })

  return { cellCurrentPlantMap, cellPlantingMap, germinatedByCellPlanting }
}

type SeedTrayCellViewProps = {
  cell?: SeedTrayCell
  total: number
  entries: Array<CellPlantingEntry>
  plants: Array<SpecificPlant>
  germinatedByCellPlanting: { [cellPlantingPk: number]: number }
  selectedCellPlantingPks: Array<number>
  onToggleGermination: (cellPlantingPk: number) => void
  onOpenMove: (plant: SpecificPlant) => void
  locationLabel: (loc: SpecificPlantLocation) => string
}

const SeedTrayCellView: React.FC<SeedTrayCellViewProps> = ({
  cell,
  total,
  entries,
  plants,
  germinatedByCellPlanting,
  selectedCellPlantingPks,
  onToggleGermination,
  onOpenMove,
  locationLabel
}) => {
  const totalGerminated = entries.reduce((sum, e) => sum + (germinatedByCellPlanting[e.cellPlantingPk] ?? 0), 0)

  return (
    <td
      style={{
        textAlign: 'center',
        minWidth: 70,
        verticalAlign: 'top',
        background: entries.some((entry) => selectedCellPlantingPks.includes(entry.cellPlantingPk)) ? '#e8f5e9' : undefined
      }}
    >
      <div>{cell ? `Cell ${cell.x_position}, ${cell.y_position}` : ''}</div>
      {cell && <div style={{ fontWeight: 'bold' }}>{total || 0} sown</div>}
      {cell && <div style={{ color: 'green', fontSize: '0.85em' }}>{totalGerminated} germinated</div>}
      {plants.map((plant) => {
        const loc = currentLocation(plant)
        const sortedLocations = [...plant.locations].sort((a, b) => new Date(a.started).getTime() - new Date(b.started).getTime())
        return (
          <div key={plant.pk} style={{ marginTop: 4, fontSize: '0.8em', borderTop: '1px solid #eee', paddingTop: 2 }}>
            <div>
              Plant #{plant.pk}
              {loc && <span style={{ color: '#555' }}> — {locationLabel(loc)}</span>}
            </div>
            <div style={{ marginTop: 2 }}>
              <PlantLifecycleBadge plant={plant} />
            </div>
            <Button size="sm" variant="outline-primary" style={{ fontSize: '0.75em', padding: '1px 4px', marginTop: 2 }} onClick={() => onOpenMove(plant)}>
              Move
            </Button>
            {plant.locations.length > 1 && (
              <details style={{ marginTop: 2 }}>
                <summary style={{ cursor: 'pointer', fontSize: '0.8em' }}>History</summary>
                {sortedLocations.map((l: SpecificPlantLocation) => (
                  <div key={l.pk} style={{ fontSize: '0.8em', color: '#444' }}>
                    {locationLabel(l)} {formatDateTime(l.started)}→{l.ended ? formatDateTime(l.ended) : 'now'}
                  </div>
                ))}
              </details>
            )}
          </div>
        )
      })}
      {cell &&
        entries.map((entry) => (
          <Button
            key={entry.cellPlantingPk}
            size="sm"
            variant={selectedCellPlantingPks.includes(entry.cellPlantingPk) ? 'success' : 'outline-success'}
            style={{ fontSize: '0.75em', padding: '1px 4px', marginTop: 4 }}
            onClick={() => onToggleGermination(entry.cellPlantingPk)}
            aria-pressed={selectedCellPlantingPks.includes(entry.cellPlantingPk)}
          >
            {selectedCellPlantingPks.includes(entry.cellPlantingPk) ? 'Selected' : '+ Germination'}
            {entries.length > 1 ? ` (#${entry.cellPlantingPk})` : ''}
          </Button>
        ))}
    </td>
  )
}

type PlantLifecycleRowProps = {
  plant: SpecificPlant
  locationLabel: (loc: SpecificPlantLocation) => string
  onOutcome: (plant: SpecificPlant, outcome: PlantOutcomeAction) => void
  onReverse: (plant: SpecificPlant, event: PlantLifecycleEvent) => void
  busy: boolean
}

const PlantLifecycleRow: React.FC<PlantLifecycleRowProps> = ({ plant, locationLabel, onOutcome, onReverse, busy }) => {
  const [showHistory, setShowHistory] = React.useState(false)
  const loc = currentLocation(plant)
  const historyQuery = useQuery({
    queryKey: queryKeys.plantings.plantLifecycle(plant.pk),
    queryFn: ({ signal }) => getSpecificPlantLifecycleEvents(plant.pk, signal),
    enabled: showHistory
  })

  return (
    <>
      <tr>
        <td>#{plant.pk}</td>
        <td>
          <PlantLifecycleBadge plant={plant} />
        </td>
        <td>{loc ? locationLabel(loc) : '—'}</td>
        <td>{plant.final_outcome_at ? formatDateTime(plant.final_outcome_at) : '—'}</td>
        <td>
          <PlantOutcomeButtons plant={plant} onOutcome={onOutcome} disabled={busy} />
        </td>
        <td>
          <Button size="sm" variant="outline-secondary" onClick={() => setShowHistory((shown) => !shown)}>
            {showHistory ? 'Hide history' : 'History'}
          </Button>
        </td>
      </tr>
      {showHistory && (
        <tr>
          <td colSpan={6}>
            {historyQuery.isPending ? (
              <span className="text-muted">Loading history…</span>
            ) : (
              <PlantLifecycleHistory events={historyQuery.data ?? []} onReverse={(event) => onReverse(plant, event)} />
            )}
          </td>
        </tr>
      )}
    </>
  )
}

type GerminationFormProps = {
  cellPlantingPks: Array<number>
  quantity: number
  date: string
  notes: string
  onChangeDate: (value: string) => void
  onChangeQuantity: (value: number) => void
  onChangeNotes: (value: string) => void
  onSave: () => void
  onCancel: () => void
}

const GerminationForm: React.FC<GerminationFormProps> = ({ cellPlantingPks, quantity, date, notes, onChangeDate, onChangeQuantity, onChangeNotes, onSave, onCancel }) => (
  <div style={{ marginTop: 16, padding: 12, border: '1px solid #ccc', maxWidth: 400 }}>
    <h5>Record Germination</h5>
    <p>
      {cellPlantingPks.length} cell{cellPlantingPks.length === 1 ? '' : 's'} selected
    </p>
    <div>
      <label>
        Quantity per cell:{' '}
        <input type="number" min={1} max={Math.floor(5000 / cellPlantingPks.length)} value={quantity} onChange={(e) => onChangeQuantity(Number(e.target.value))} />
      </label>
    </div>
    <div>
      <label>
        Date: <input type="datetime-local" value={date} onChange={(e) => onChangeDate(e.target.value)} />
      </label>
    </div>
    <div style={{ marginTop: 8 }}>
      <label>
        Notes: <input type="text" value={notes} onChange={(e) => onChangeNotes(e.target.value)} placeholder="Optional" />
      </label>
    </div>
    <div style={{ marginTop: 8 }}>
      <Button variant="success" onClick={onSave} disabled={!date || quantity < 1 || cellPlantingPks.length * quantity > 5000}>
        Review {cellPlantingPks.length * quantity} plant{cellPlantingPks.length * quantity === 1 ? '' : 's'}
      </Button>{' '}
      <Button variant="secondary" onClick={onCancel}>
        Cancel
      </Button>
    </div>
  </div>
)

type MovePlantFormProps = {
  form: MoveForm
  gardenSquares: GardenSquare[] | undefined
  allSeedTrays: SeedTray[] | undefined
  moveCells: SeedTrayCell[] | undefined
  moveCellsLoading: boolean | undefined
  onChange: (form: MoveForm) => void
  onChangeTray: (value: string) => void
  onSave: () => void
  onCancel: () => void
}

const MovePlantForm: React.FC<MovePlantFormProps> = ({ form, gardenSquares, allSeedTrays, moveCells, moveCellsLoading, onChange, onChangeTray, onSave, onCancel }) => (
  <div style={{ marginTop: 16, padding: 12, border: '1px solid #ccc', maxWidth: 480 }}>
    <h5>Move Plant #{form.plantPk}</h5>
    {form.currentLocationPk && <p style={{ fontSize: '0.9em', color: '#666' }}>This will end the current location on the selected date.</p>}
    <div>
      <label>
        Move to:{' '}
        <select
          value={form.locationType}
          onChange={(e) => {
            const locationType = e.target.value as MoveForm['locationType']
            if (locationType === 'garden_square') {
              onChange({
                plantPk: form.plantPk,
                currentLocationPk: form.currentLocationPk,
                date: form.date,
                notes: form.notes,
                locationType: 'garden_square',
                gardenSquarePk: undefined
              })
            } else {
              onChange({
                plantPk: form.plantPk,
                currentLocationPk: form.currentLocationPk,
                date: form.date,
                notes: form.notes,
                locationType: 'seed_tray_cell',
                moveSeedTrayPk: undefined,
                seedTrayCellPk: undefined
              })
            }
          }}
        >
          <option value="garden_square">Garden Square</option>
          <option value="seed_tray_cell">Seed Tray Cell</option>
        </select>
      </label>
    </div>
    {form.locationType === 'garden_square' && (
      <div style={{ marginTop: 8 }}>
        <label>
          Garden Square:{' '}
          <select value={form.gardenSquarePk ?? ''} onChange={(e) => onChange({ ...form, gardenSquarePk: Number(e.target.value) })}>
            <option value="">— select —</option>
            {gardenSquares?.map((sq) => (
              <option key={sq.pk} value={sq.pk}>
                {sq.name}
              </option>
            ))}
          </select>
        </label>
      </div>
    )}
    {form.locationType === 'seed_tray_cell' && (
      <>
        <div style={{ marginTop: 8 }}>
          <label>
            Seed Tray:{' '}
            <select value={form.moveSeedTrayPk ?? ''} onChange={(e) => onChangeTray(e.target.value)}>
              <option value="">— select tray —</option>
              {allSeedTrays?.map((t) => (
                <option key={t.pk} value={t.pk}>
                  Tray #{t.pk} ({formatDate(t.created)})
                </option>
              ))}
            </select>
          </label>
        </div>
        {form.moveSeedTrayPk && (
          <div style={{ marginTop: 8 }}>
            <label>
              Cell:{' '}
              {moveCellsLoading ? (
                <span>Loading…</span>
              ) : (
                <select value={form.seedTrayCellPk ?? ''} onChange={(e) => onChange({ ...form, seedTrayCellPk: Number(e.target.value) })}>
                  <option value="">— select cell —</option>
                  {moveCells?.map((cell) => (
                    <option key={cell.pk} value={cell.pk}>
                      ({cell.x_position},{cell.y_position})
                    </option>
                  ))}
                </select>
              )}
            </label>
          </div>
        )}
      </>
    )}
    <div style={{ marginTop: 8 }}>
      <label>
        Date: <input type="datetime-local" value={form.date} onChange={(e) => onChange({ ...form, date: e.target.value })} />
      </label>
    </div>
    <div style={{ marginTop: 8 }}>
      <label>
        Notes: <input type="text" value={form.notes} onChange={(e) => onChange({ ...form, notes: e.target.value })} placeholder="Optional" />
      </label>
    </div>
    <div style={{ marginTop: 8 }}>
      <Button variant="primary" onClick={onSave} disabled={!form.date || (form.locationType === 'garden_square' ? !form.gardenSquarePk : !form.seedTrayCellPk)}>
        Save
      </Button>{' '}
      <Button variant="secondary" onClick={onCancel}>
        Cancel
      </Button>
    </div>
  </div>
)

type GenerationCardProps = {
  generations: Array<SeedTrayGeneration>
  active?: SeedTrayGeneration
  busy: boolean
  cleaning: boolean
  onFill: () => void
  onStartClean: () => void
  onCancelClean: () => void
  onReview: (generation: SeedTrayGeneration) => void
  onReopen: (generation: SeedTrayGeneration) => void
}

const GenerationCard: React.FC<GenerationCardProps> = ({ generations, active, busy, cleaning, onFill, onStartClean, onCancelClean, onReview, onReopen }) => {
  const closed = generations.filter((generation) => generation.status === 'closed')
  const needsReview = active?.review_state === 'needs_review'

  return (
    <Card className="mb-3">
      <Card.Body>
        <Card.Title>Tray generation</Card.Title>
        {active ? (
          <>
            <p className="mb-2">
              Filled as <strong>{active.code}</strong> on {formatDateTime(active.opened_at)}. Media and sowings recorded from now on belong to this fill.
            </p>
            {active.notes && <p className="text-muted mb-2">{active.notes}</p>}
            {needsReview && (
              <Alert variant="warning">
                <p className="mb-2">This fill was migrated from sowings recorded before generations existed, so it may cover more than one cultivation cycle.</p>
                {active.review_details && <pre className="mb-2 small text-wrap">{active.review_details}</pre>}
                <Button size="sm" variant="outline-dark" onClick={() => onReview(active)} disabled={busy}>
                  Confirm this is one fill
                </Button>
              </Alert>
            )}
          </>
        ) : (
          <p className="mb-2 text-muted">The tray is empty. Fill it before sowing into it or applying media to its cells.</p>
        )}
        <div className="d-flex gap-2 flex-wrap">
          {!active && (
            <Button size="sm" onClick={onFill} disabled={busy}>
              Fill this tray
            </Button>
          )}
          {active && !cleaning && (
            <Button size="sm" variant="warning" onClick={onStartClean} disabled={busy || needsReview}>
              Clean this tray
            </Button>
          )}
          {active && cleaning && (
            <Button size="sm" variant="secondary" onClick={onCancelClean} disabled={busy}>
              Cancel clean
            </Button>
          )}
        </div>
        {closed.length > 0 && (
          <details className="mt-3">
            <summary style={{ cursor: 'pointer' }}>Earlier fills ({closed.length})</summary>
            <Table size="sm" responsive className="mt-2">
              <thead>
                <tr>
                  <th>Fill</th>
                  <th>Opened</th>
                  <th>Cleaned</th>
                  <th>Reason</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {closed.map((generation) => (
                  <tr key={generation.pk}>
                    <td>{generation.code}</td>
                    <td>{formatDateTime(generation.opened_at)}</td>
                    <td>{generation.closed_at ? formatDateTime(generation.closed_at) : ''}</td>
                    <td>{generation.close_reason}</td>
                    <td>
                      {!active && (
                        <Button size="sm" variant="outline-secondary" onClick={() => onReopen(generation)} disabled={busy}>
                          Correct this clean
                        </Button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </Table>
          </details>
        )}
      </Card.Body>
    </Card>
  )
}

function SeedTrayDetails({ seedTrayPk }: SeedTrayDetailsProps) {
  const cache = useQueryClient()
  const [selectedCellPlantingPks, setSelectedCellPlantingPks] = React.useState<Array<number>>([])
  const [germinationDate, setGerminationDate] = React.useState(localDatetimeInputValue())
  const [germinationQuantity, setGerminationQuantity] = React.useState(1)
  const [germinationNotes, setGerminationNotes] = React.useState('')
  const [moveForm, setMoveForm] = React.useState<MoveForm>()
  const [inventoryAction, setInventoryAction] = React.useState<InventoryAction>()
  const [inventoryDestination, setInventoryDestination] = React.useState<number>()
  const [inventoryReason, setInventoryReason] = React.useState('')
  const [inventoryCost, setInventoryCost] = React.useState('0.0000')
  const [cleaning, setCleaning] = React.useState(false)
  const [applyingFillInput, setApplyingFillInput] = React.useState(false)
  const seedTrayModelsQuery = useQuery({
    queryKey: queryKeys.seedTrays.models,
    queryFn: ({ signal }) => getSeedTrayModels(signal)
  })
  const seedTraysQuery = useQuery({
    queryKey: queryKeys.seedTrays.trays,
    queryFn: ({ signal }) => getSeedTrays(signal)
  })
  const selectedSeedTray = seedTraysQuery.data?.find((tray) => tray.pk === seedTrayPk)
  const inventoryLocationsQuery = useQuery({
    queryKey: queryKeys.locations.list('active'),
    queryFn: ({ signal }) => getLocations(signal, true)
  })
  const inventoryMovementsQuery = useQuery({
    queryKey: queryKeys.seedTrays.movements(selectedSeedTray?.inventory_unit ?? 0),
    queryFn: ({ signal }) => getSerializedUnitMovements(selectedSeedTray?.inventory_unit as number, signal),
    enabled: Boolean(selectedSeedTray?.inventory_unit)
  })
  const seedTrayCellsQuery = useQuery({
    queryKey: queryKeys.seedTrays.cells(seedTrayPk),
    queryFn: ({ signal }) => getSeedTrayCells(seedTrayPk, signal)
  })
  const plantingsQuery = useQuery({
    queryKey: queryKeys.plantings.seedTray(seedTrayPk),
    queryFn: ({ signal }) => getPlantingSeedTray(seedTrayPk, signal)
  })
  const seedPacketsQuery = useQuery({
    queryKey: queryKeys.seeds.packets.current,
    queryFn: ({ signal }) => getSeedPacketsCurrent(signal)
  })
  const specificPlantsQuery = useQuery({
    queryKey: queryKeys.plantings.specificPlants(seedTrayPk),
    queryFn: ({ signal }) => getSpecificPlantsBySeedTray(seedTrayPk, signal)
  })
  const gardenSquaresQuery = useQuery({
    queryKey: queryKeys.garden.squares,
    queryFn: ({ signal }) => getGardenSquares(signal)
  })
  const generationsQuery = useQuery({
    queryKey: queryKeys.seedTrays.generations(seedTrayPk),
    queryFn: ({ signal }) => getSeedTrayGenerations(seedTrayPk, signal)
  })
  const generations = generationsQuery.data ?? []
  const activeGeneration = generations.find((generation) => generation.status === 'open')
  const cleanContentsQuery = useQuery({
    queryKey: queryKeys.seedTrays.generationContents(activeGeneration?.pk ?? 0),
    queryFn: ({ signal }) => getSeedTrayGenerationContents(activeGeneration?.pk as number, signal),
    enabled: cleaning && Boolean(activeGeneration)
  })
  const generationCostQuery = useQuery({
    queryKey: queryKeys.seedTrays.generationCost(activeGeneration?.pk ?? 0),
    queryFn: ({ signal }) => getSeedTrayGenerationCost(activeGeneration?.pk as number, signal),
    enabled: Boolean(activeGeneration)
  })
  const moveTrayPk = moveForm?.locationType === 'seed_tray_cell' ? moveForm.moveSeedTrayPk : undefined
  const moveCellsQuery = useQuery({
    queryKey: queryKeys.seedTrays.cells(moveTrayPk ?? 0),
    queryFn: ({ signal }) => getSeedTrayCells(moveTrayPk as number, signal),
    enabled: Boolean(moveTrayPk)
  })
  const germinationMutation = useMutation({
    mutationFn: postBulkPlantOperation,
    onSuccess: () =>
      Promise.all([
        cache.invalidateQueries({ queryKey: queryKeys.plantings.specificPlants(seedTrayPk) }),
        cache.invalidateQueries({ queryKey: queryKeys.plantings.currentSeedTrays })
      ])
  })
  const moveMutation = useMutation({
    mutationFn: ({ plantPk, move }: { plantPk: number; move: SpecificPlantMove }) => moveSpecificPlant(plantPk, move),
    onSuccess: () =>
      Promise.all([
        cache.invalidateQueries({ queryKey: queryKeys.plantings.specificPlantsAll }),
        cache.invalidateQueries({ queryKey: queryKeys.plantings.currentSeedTrays }),
        cache.invalidateQueries({ queryKey: queryKeys.plantings.currentGardenSquares }),
        cache.invalidateQueries({ queryKey: queryKeys.seeds.packets.all })
      ])
  })
  const outcomeMutation = useMutation({
    mutationFn: ({ plantPk, outcome, reason }: { plantPk: number; outcome: PlantOutcomeAction; reason?: string }) =>
      postSpecificPlantOutcome(plantPk, outcome, reason ? { reason } : {}),
    onSuccess: (_event, variables) => invalidatePlantLifecycle(variables.plantPk)
  })
  const reverseMutation = useMutation({
    mutationFn: ({ plantPk, event, reason }: { plantPk: number; event: number; reason: string }) => reverseSpecificPlantEvent(plantPk, { event, reason }),
    onSuccess: (_event, variables) => invalidatePlantLifecycle(variables.plantPk)
  })
  // Filling, cleaning, and correcting all change which sowings and plants the
  // tray shows, so each one revalidates the same family of keys.
  function invalidateGenerations() {
    return Promise.all([
      cache.invalidateQueries({ queryKey: queryKeys.seedTrays.generationsAll }),
      cache.invalidateQueries({ queryKey: queryKeys.seedTrays.trays }),
      cache.invalidateQueries({ queryKey: queryKeys.plantings.seedTray(seedTrayPk) }),
      cache.invalidateQueries({ queryKey: queryKeys.plantings.specificPlants(seedTrayPk) }),
      cache.invalidateQueries({ queryKey: queryKeys.plantings.currentSeedTrays })
    ])
  }
  const fillMutation = useMutation({
    mutationFn: ({ notes }: { notes: string }) => openSeedTrayGeneration(seedTrayPk, notes),
    onSuccess: invalidateGenerations
  })
  const cleanMutation = useMutation({
    mutationFn: ({ generation, request }: { generation: number; request: CleanGenerationRequestPayload }) => cleanSeedTrayGeneration(generation, request),
    onSuccess: invalidateGenerations
  })
  const reopenMutation = useMutation({
    mutationFn: ({ generation, reason }: { generation: number; reason: string }) => reopenSeedTrayGeneration(generation, reason),
    onSuccess: invalidateGenerations
  })
  const reviewMutation = useMutation({
    mutationFn: ({ generation, reason }: { generation: number; reason: string }) => reviewSeedTrayGeneration(generation, reason),
    onSuccess: invalidateGenerations
  })
  const inventoryMutation = useMutation({
    mutationFn: ({ unit, action, data }: { unit: number; action: InventoryAction; data: object }) => postSerializedUnitAction(unit, action, data),
    onSuccess: () =>
      Promise.all([
        cache.invalidateQueries({ queryKey: queryKeys.seedTrays.trays }),
        cache.invalidateQueries({ queryKey: queryKeys.seedTrays.movements(selectedSeedTray?.inventory_unit ?? 0) })
      ])
  })

  const seedTrayModels = seedTrayModelsQuery.data ?? []
  const seedTrays = seedTraysQuery.data ?? []
  const allCells = seedTrayCellsQuery.data ?? []
  const plantings = plantingsQuery.data ?? []
  const seedPacketDetails = seedPacketsQuery.data ?? []
  const specificPlants = specificPlantsQuery.data ?? []
  const gardenSquares = gardenSquaresQuery.data ?? []
  const inventoryLocations = inventoryLocationsQuery.data ?? []
  const inventoryDestinations = inventoryLocations.filter((location) => location.code !== 'SYSTEM-TRAY-UNKNOWN' && location.location_type !== 'seed_packet')
  const inventoryMovements = inventoryMovementsQuery.data ?? []
  const inventoryLocationMap = inventoryLocations.reduce<Record<number, string>>((locations, location) => {
    locations[location.pk] = location.name
    return locations
  }, {})
  const seedTray = selectedSeedTray
  const seedTrayModel = seedTrayModels.find((model) => model.pk === seedTray?.model)
  const seedTrayCells = buildSeedTrayCellGrid(seedTrayModel, allCells)
  const seeds = seedPacketDetails.reduce<Record<number, SeedPacketDetails>>((packets, packet) => {
    packets[packet.pk] = packet
    return packets
  }, {})
  const { cellCurrentPlantMap, cellPlantingMap, germinatedByCellPlanting, cellTotals } = computeCellData(specificPlants, plantings)
  const isLoading = [
    seedTrayModelsQuery,
    seedTraysQuery,
    seedTrayCellsQuery,
    plantingsQuery,
    seedPacketsQuery,
    specificPlantsQuery,
    gardenSquaresQuery,
    inventoryLocationsQuery
  ].some((query) => query.isPending)

  async function handleRecordGermination() {
    if (selectedCellPlantingPks.length === 0) return
    const parsedDate = parseLocalDatetimeInput(germinationDate)
    if (!parsedDate) return
    const request: BulkPlantOperationRequest = {
      idempotency_key: globalThis.crypto.randomUUID(),
      action: 'germinate',
      atomicity: 'all_or_nothing',
      occurred_at: parsedDate.toISOString(),
      reason: germinationNotes,
      plants: [],
      selection_source: { mode: 'cell_plantings', cell_plantings: selectedCellPlantingPks },
      action_payload: {
        cell_plantings: selectedCellPlantingPks,
        quantity: germinationQuantity,
        notes: germinationNotes
      }
    }
    const review = await previewBulkPlantOperation(request)
    if (review.conflicts > 0) {
      globalThis.alert(review.source?.conflicts.join(' ') || 'This germination cannot be recorded.')
      return
    }
    if (!globalThis.confirm(`Create ${review.eligible} individually tracked plant${review.eligible === 1 ? '' : 's'}?`)) return
    await germinationMutation.mutateAsync(request)
    setSelectedCellPlantingPks([])
    setGerminationQuantity(1)
    setGerminationNotes('')
  }

  function toggleGerminationSelection(cellPlantingPk: number) {
    setSelectedCellPlantingPks((selected) => (selected.includes(cellPlantingPk) ? selected.filter((pk) => pk !== cellPlantingPk) : [...selected, cellPlantingPk]))
  }

  async function handleRecordMove() {
    if (!moveForm) return
    const parsedMoveDate = parseLocalDatetimeInput(moveForm.date)
    if (!parsedMoveDate) return
    await moveMutation.mutateAsync({
      plantPk: moveForm.plantPk,
      move: {
        location_type: moveForm.locationType,
        seed_tray_cell: moveForm.locationType === 'seed_tray_cell' ? moveForm.seedTrayCellPk : undefined,
        garden_square: moveForm.locationType === 'garden_square' ? moveForm.gardenSquarePk : undefined,
        started: parsedMoveDate.toISOString(),
        notes: moveForm.notes || undefined
      }
    })
    setMoveForm(undefined)
  }

  function invalidatePlantLifecycle(plantPk: number) {
    return Promise.all([
      cache.invalidateQueries({ queryKey: queryKeys.plantings.plantLifecycle(plantPk) }),
      cache.invalidateQueries({ queryKey: queryKeys.plantings.specificPlantsAll }),
      cache.invalidateQueries({ queryKey: queryKeys.plantings.batchesAll }),
      cache.invalidateQueries({ queryKey: queryKeys.plantings.currentSeedTrays }),
      cache.invalidateQueries({ queryKey: queryKeys.plantings.currentGardenSquares })
    ])
  }

  // A backward fact says the situation changed, so the server requires a
  // reason for it. The correction below asks a deliberately different question:
  // it claims the fact was never true at all.
  async function handleRecordOutcome(plant: SpecificPlant, outcome: PlantOutcomeAction) {
    if (!REASON_REQUIRED_ACTIONS.includes(outcome)) {
      await outcomeMutation.mutateAsync({ plantPk: plant.pk, outcome })
      return
    }
    const reason = globalThis.prompt(REASON_PROMPTS[outcome])
    if (!reason || !reason.trim()) return
    await outcomeMutation.mutateAsync({ plantPk: plant.pk, outcome, reason })
  }

  async function handleReverseEvent(plant: SpecificPlant, event: PlantLifecycleEvent) {
    const reason = globalThis.prompt('Why was this recorded in error? To record that the situation has since changed, use the outcome buttons instead.')
    if (!reason || !reason.trim()) return
    await reverseMutation.mutateAsync({ plantPk: plant.pk, event: event.pk, reason })
  }

  function openMoveForm(plant: SpecificPlant) {
    const current = currentLocation(plant)
    setMoveForm({
      plantPk: plant.pk,
      currentLocationPk: current?.pk,
      locationType: 'garden_square',
      gardenSquarePk: undefined,
      date: localDatetimeInputValue(),
      notes: ''
    })
  }

  function handleMoveTrayChange(rawValue: string) {
    setMoveForm((currentForm) => {
      if (!currentForm || currentForm.locationType !== 'seed_tray_cell') return currentForm
      return {
        ...currentForm,
        moveSeedTrayPk: rawValue ? Number(rawValue) : undefined,
        seedTrayCellPk: undefined
      }
    })
  }

  function locationLabel(location: SpecificPlantLocation): string {
    if (location.location_type === 'seed_tray_cell') {
      const cell = allCells.find((candidate) => candidate.pk === location.seed_tray_cell)
      return cell ? `Cell (${cell.x_position},${cell.y_position})` : `Cell #${location.seed_tray_cell}`
    }
    const square = gardenSquares.find((candidate) => candidate.pk === location.garden_square)
    return square ? square.name : `Square #${location.garden_square}`
  }

  async function handleFillTray() {
    await fillMutation.mutateAsync({ notes: '' })
    setApplyingFillInput(true)
  }

  function handleFillInputPosted() {
    setApplyingFillInput(false)
    if (!activeGeneration) return
    void Promise.all([
      cache.invalidateQueries({ queryKey: queryKeys.seedTrays.generationContents(activeGeneration.pk) }),
      cache.invalidateQueries({ queryKey: queryKeys.seedTrays.generationCost(activeGeneration.pk) })
    ])
  }

  async function handleReviewGeneration(generation: SeedTrayGeneration) {
    const reason = globalThis.prompt('What did you check to confirm this is one fill?')
    if (!reason || !reason.trim()) return
    await reviewMutation.mutateAsync({ generation: generation.pk, reason })
  }

  async function handleReopenGeneration(generation: SeedTrayGeneration) {
    const reason = globalThis.prompt(`Why was cleaning ${generation.code} a mistake?`)
    if (!reason || !reason.trim()) return
    await reopenMutation.mutateAsync({ generation: generation.pk, reason })
  }

  async function handleCleanGeneration(request: {
    reason: string
    plants: Array<CleanPlantDisposition>
    seeds: Array<CleanSeedDisposition>
    media: Array<CleanMediaDisposition>
    openNext: boolean
  }) {
    const contents = cleanContentsQuery.data
    if (!activeGeneration || !contents) return
    await cleanMutation.mutateAsync({
      generation: activeGeneration.pk,
      request: {
        reason: request.reason,
        // Echoed straight back from the contents this form was built on, so a
        // tray that changed underneath the operator is refused rather than
        // cleaned against decisions they never made.
        digest: contents.digest,
        plants: request.plants,
        seeds: request.seeds,
        media: request.media,
        open_next: request.openNext
      }
    })
    setCleaning(false)
  }

  async function handleInventoryAction() {
    if (!seedTray || !inventoryAction) return
    const needsDestination = inventoryAction === 'transfer' || inventoryAction === 'return' || inventoryAction === 'reconcile-opening'
    if (needsDestination && !inventoryDestination) return
    const data: Record<string, string | number> = { reason: inventoryReason }
    if (inventoryDestination) data.destination = inventoryDestination
    if (inventoryAction === 'reconcile-opening') data.acquisition_cost = inventoryCost
    await inventoryMutation.mutateAsync({
      unit: seedTray.inventory_unit,
      action: inventoryAction,
      data
    })
    setInventoryAction(undefined)
    setInventoryDestination(undefined)
    setInventoryReason('')
  }

  if (isLoading) {
    return <div>Loading...</div>
  }

  if (!seedTray) {
    return <div>Seed tray not found.</div>
  }

  return (
    <div>
      <h1>Seed Tray Details (ID: {seedTray.pk})</h1>
      <p>
        Model: {seedTrayModel?.identifier} ({seedTrayModel?.description})
      </p>
      <p>Created: {formatDate(seedTray.created)}</p>
      <p>Notes: {seedTray.notes}</p>
      <GenerationCard
        generations={generations}
        active={activeGeneration}
        busy={fillMutation.isPending || cleanMutation.isPending || reopenMutation.isPending || reviewMutation.isPending}
        cleaning={cleaning}
        onFill={handleFillTray}
        onStartClean={() => setCleaning(true)}
        onCancelClean={() => setCleaning(false)}
        onReview={handleReviewGeneration}
        onReopen={handleReopenGeneration}
      />
      {cleaning &&
        activeGeneration &&
        (cleanContentsQuery.isPending ? (
          <p className="text-muted">Reading what is still in the tray…</p>
        ) : (
          cleanContentsQuery.data && (
            <GenerationCleanForm
              contents={cleanContentsQuery.data}
              locations={inventoryDestinations}
              busy={cleanMutation.isPending}
              onCancel={() => setCleaning(false)}
              onConfirm={handleCleanGeneration}
            />
          )
        ))}
      <Card className="mb-3">
        <Card.Body>
          <Card.Title>Inputs</Card.Title>
          <p className="mb-2">Media and treatments applied to this tray are recorded against its cells and attributed to the fill those cells are serving.</p>
          {activeGeneration ? (
            <Button variant="outline-primary" onClick={() => setApplyingFillInput((shown) => !shown)}>
              {applyingFillInput ? 'Hide input form' : `Apply an input to ${activeGeneration.code}`}
            </Button>
          ) : (
            <Alert variant="secondary" className="mb-0">
              Fill the tray before applying media to its cells, so the media has a fill to belong to.
            </Alert>
          )}
        </Card.Body>
      </Card>
      {activeGeneration && applyingFillInput && (
        <div>
          <InputApplicationForm targets={[]} tray={seedTray.pk} title={`Apply the fill input to ${activeGeneration.code}`} onPosted={handleFillInputPosted} />
          <Button className="mb-4" variant="outline-secondary" onClick={() => setApplyingFillInput(false)}>
            Skip for now
          </Button>
        </div>
      )}
      {activeGeneration && generationCostQuery.data && (
        <Card className="mb-3">
          <Card.Body>
            <Card.Title>Media cost of {activeGeneration.code}</Card.Title>
            <GenerationCostPanel breakdown={generationCostQuery.data} />
          </Card.Body>
        </Card>
      )}
      <Card className="mb-3">
        <Card.Body>
          <Card.Title>Physical inventory</Card.Title>
          {seedTray.inventory.reconciliation_required && <Alert variant="warning">Opening cost and physical location require reconciliation.</Alert>}
          <dl className="row mb-2">
            <dt className="col-sm-3">Asset code</dt>
            <dd className="col-sm-9">{seedTray.inventory.asset_code}</dd>
            <dt className="col-sm-3">State</dt>
            <dd className="col-sm-9">
              {seedTray.inventory.physical_state} {seedTray.inventory.in_use ? '(in use)' : '(not in use)'}
            </dd>
            <dt className="col-sm-3">Location</dt>
            <dd className="col-sm-9">{seedTray.inventory.current_location ? inventoryLocationMap[seedTray.inventory.current_location] : 'Off hand'}</dd>
            <dt className="col-sm-3">Acquisition cost</dt>
            <dd className="col-sm-9">{seedTray.inventory.acquisition_cost === null ? 'Unknown' : `${seedTray.inventory.acquisition_cost} ${seedTray.inventory.currency_code}`}</dd>
            <dt className="col-sm-3">Source lot</dt>
            <dd className="col-sm-9">
              #{seedTray.inventory.source_lot}
              {seedTray.inventory.receipt_line ? ` / receipt line #${seedTray.inventory.receipt_line}` : ' / opening balance'}
            </dd>
          </dl>
          <div className="d-flex gap-2 flex-wrap">
            {seedTray.inventory.current_location && !seedTray.inventory.reconciliation_required && (
              <Button size="sm" onClick={() => setInventoryAction('transfer')}>
                Transfer
              </Button>
            )}
            {seedTray.inventory.current_location && !seedTray.inventory.in_use && !seedTray.inventory.reconciliation_required && (
              <>
                <Button size="sm" variant="warning" onClick={() => setInventoryAction('loss')}>
                  Record loss
                </Button>
                <Button size="sm" variant="secondary" onClick={() => setInventoryAction('retire')}>
                  Retire
                </Button>
              </>
            )}
            {(seedTray.inventory.physical_state === 'lost' || seedTray.inventory.physical_state === 'retired') && (
              <Button size="sm" variant="success" onClick={() => setInventoryAction('return')}>
                Return to stock
              </Button>
            )}
            {seedTray.inventory.reconciliation_required && (
              <Button size="sm" variant="outline-primary" onClick={() => setInventoryAction('reconcile-opening')}>
                Reconcile opening
              </Button>
            )}
          </div>
          {inventoryAction && (
            <Form className="border rounded p-2 mt-3">
              <strong>{inventoryAction.replace('-', ' ')}</strong>
              {(inventoryAction === 'transfer' || inventoryAction === 'return' || inventoryAction === 'reconcile-opening') && (
                <Form.Group className="mt-2">
                  <Form.Label>Destination</Form.Label>
                  <Form.Select value={inventoryDestination ?? ''} onChange={(event) => setInventoryDestination(event.target.value ? Number(event.target.value) : undefined)}>
                    <option value="">Select location</option>
                    {inventoryDestinations.map((location) => (
                      <option key={location.pk} value={location.pk}>
                        {location.name}
                      </option>
                    ))}
                  </Form.Select>
                </Form.Group>
              )}
              {inventoryAction === 'reconcile-opening' && (
                <Form.Group className="mt-2">
                  <Form.Label>Acquisition cost ({seedTray.inventory.currency_code})</Form.Label>
                  <Form.Control type="number" min={0} step="0.0001" value={inventoryCost} onChange={(event) => setInventoryCost(event.target.value)} />
                </Form.Group>
              )}
              <Form.Group className="mt-2">
                <Form.Label>Reason</Form.Label>
                <Form.Control value={inventoryReason} onChange={(event) => setInventoryReason(event.target.value)} />
              </Form.Group>
              <Button
                className="mt-2"
                size="sm"
                onClick={handleInventoryAction}
                disabled={
                  inventoryMutation.isPending ||
                  ((inventoryAction === 'loss' || inventoryAction === 'retire' || inventoryAction === 'return' || inventoryAction === 'reconcile-opening') &&
                    !inventoryReason.trim())
                }
              >
                Save action
              </Button>{' '}
              <Button className="mt-2" size="sm" variant="secondary" onClick={() => setInventoryAction(undefined)}>
                Cancel
              </Button>
            </Form>
          )}
          <h6 className="mt-3">Movement history</h6>
          <Table size="sm">
            <thead>
              <tr>
                <th>Date</th>
                <th>Type</th>
                <th>From</th>
                <th>To</th>
                <th>Reason/reference</th>
              </tr>
            </thead>
            <tbody>
              {inventoryMovements.map((movement) => (
                <tr key={movement.pk}>
                  <td>{formatDateTime(movement.occurred_at)}</td>
                  <td>{movement.movement_type}</td>
                  <td>{movement.source ? inventoryLocationMap[movement.source] : ''}</td>
                  <td>{movement.destination ? inventoryLocationMap[movement.destination] : ''}</td>
                  <td>{movement.reason || movement.reference}</td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Card.Body>
      </Card>
      Plantings:
      <Table border={1} cellPadding={5} cellSpacing={0}>
        <thead>
          <tr>
            <th>Planting ID</th>
            <th>Planted On</th>
            <th>Seeds / clusters sown</th>
            <th>Seeds Used</th>
            <th>Notes</th>
            <th>Removed</th>
          </tr>
        </thead>
        <tbody>
          {plantings.map((planting) => {
            const packet = seeds[planting.seeds_used]
            return (
              <tr key={planting.pk}>
                <td>{planting.pk}</td>
                <td>{formatDate(planting.planted)}</td>
                <td>{planting.quantity} sown</td>
                <td>
                  {packet?.plant} - {packet?.variety}
                </td>
                <td>{planting.notes}</td>
                <td>{planting.removed ? 'Yes' : ''}</td>
              </tr>
            )
          })}
        </tbody>
      </Table>
      <div className="d-flex align-items-center gap-2 mb-2">
        <span className="text-muted">Select one or more sown cells to record germination.</span>
        <Button
          size="sm"
          variant="outline-success"
          onClick={() =>
            setSelectedCellPlantingPks(
              Object.values(cellPlantingMap)
                .flat()
                .map((entry) => entry.cellPlantingPk)
            )
          }
          disabled={Object.keys(cellPlantingMap).length === 0}
        >
          Select all sown cells
        </Button>
        {selectedCellPlantingPks.length > 0 && (
          <Button size="sm" variant="outline-secondary" onClick={() => setSelectedCellPlantingPks([])}>
            Clear selection
          </Button>
        )}
      </div>
      <Table border={2} cellPadding={5} cellSpacing={0}>
        <tbody>
          {seedTrayModel &&
            seedTrayCells.map((row, rowIndex) => (
              <tr key={rowIndex}>
                {row.map((cell, cellIndex) => (
                  <SeedTrayCellView
                    key={cellIndex}
                    cell={cell}
                    total={cell ? cellTotals[cell.pk] || 0 : 0}
                    entries={cell ? (cellPlantingMap[cell.pk] ?? []) : []}
                    plants={cell ? (cellCurrentPlantMap[cell.pk] ?? []) : []}
                    germinatedByCellPlanting={germinatedByCellPlanting}
                    selectedCellPlantingPks={selectedCellPlantingPks}
                    onToggleGermination={toggleGerminationSelection}
                    onOpenMove={openMoveForm}
                    locationLabel={locationLabel}
                  />
                ))}
              </tr>
            ))}
        </tbody>
      </Table>
      {selectedCellPlantingPks.length > 0 && (
        <GerminationForm
          cellPlantingPks={selectedCellPlantingPks}
          quantity={germinationQuantity}
          date={germinationDate}
          notes={germinationNotes}
          onChangeDate={setGerminationDate}
          onChangeQuantity={setGerminationQuantity}
          onChangeNotes={setGerminationNotes}
          onSave={handleRecordGermination}
          onCancel={() => setSelectedCellPlantingPks([])}
        />
      )}
      <Card className="mb-3">
        <Card.Body>
          <Card.Title>Plants</Card.Title>
          {specificPlants.length === 0 ? (
            <p className="text-muted mb-0">No germinations recorded for this tray yet.</p>
          ) : (
            <Table size="sm" responsive>
              <thead>
                <tr>
                  <th>Plant</th>
                  <th>State</th>
                  <th>Location</th>
                  <th>Resolved</th>
                  <th>Record outcome</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {specificPlants.map((plant) => (
                  <PlantLifecycleRow
                    key={plant.pk}
                    plant={plant}
                    locationLabel={locationLabel}
                    onOutcome={handleRecordOutcome}
                    onReverse={handleReverseEvent}
                    busy={outcomeMutation.isPending || reverseMutation.isPending}
                  />
                ))}
              </tbody>
            </Table>
          )}
        </Card.Body>
      </Card>
      {moveForm && (
        <MovePlantForm
          form={moveForm}
          gardenSquares={gardenSquares}
          allSeedTrays={seedTrays}
          moveCells={moveCellsQuery.data}
          moveCellsLoading={moveCellsQuery.isPending}
          onChange={setMoveForm}
          onChangeTray={handleMoveTrayChange}
          onSave={handleRecordMove}
          onCancel={() => setMoveForm(undefined)}
        />
      )}
    </div>
  )
}

function showSeedTrayDetails(elem: string, seedTrayPk: number) {
  const element = document.getElementById(elem)
  if (!element) {
    throw new Error(`Cannot show seed tray details: element #${elem} was not found`)
  }
  const root = ReactDOM.createRoot(element)
  root.render(
    <QueryClientProvider client={queryClient}>
      <ApiErrorAlert />
      <SeedTrayDetails seedTrayPk={seedTrayPk} />
    </QueryClientProvider>
  )
}

;(globalThis as Record<string, unknown>).showSeedTrayDetails = showSeedTrayDetails

export { SeedTrayDetails }
