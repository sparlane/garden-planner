import React from 'react'
import * as ReactDOM from 'react-dom/client'
import { QueryClientProvider, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { SeedTray, SeedTrayCell, SeedTrayModel } from '../types/seedtrays'
import { localDatetimeInputValue, parseLocalDatetimeInput, formatDate, formatDateTime } from '../utils'
import { getSeedTrayModels, getSeedTrays, getSeedTrayCells, getSerializedUnitMovements, postSerializedUnitAction } from '../api/seedtrays'
import { Alert, Button, Card, Form, Table } from 'react-bootstrap'
import { SeedTrayPlanting, SpecificPlant, SpecificPlantLocation, SpecificPlantMove } from '../types/plantings'
import { getPlantingSeedTray, getSpecificPlantsBySeedTray, addSpecificPlant, moveSpecificPlant } from '../api/plantings'
import { ApiErrorAlert } from '../api_error_alert'
import { SeedPacketDetails } from '../types/seeds'
import { getSeedPacketsCurrent } from '../api/seeds'
import { GardenSquare } from '../types/garden'
import { getGardenSquares } from '../api/garden'
import { getInventoryLocations } from '../api/inventory'
import { queryClient, queryKeys } from '../query'

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

function buildCellGrid(model: SeedTrayModel | undefined, cells: Array<SeedTrayCell>): (SeedTrayCell | undefined)[][] {
  if (!model) return []
  const grid: (SeedTrayCell | undefined)[][] = Array.from({ length: model.x_cells }, () => Array.from({ length: model.y_cells }, () => undefined))
  cells.forEach((cell) => {
    if (cell.x_position < 0 || cell.x_position >= model.x_cells) return
    if (cell.y_position < 0 || cell.y_position >= model.y_cells) return
    if (grid[cell.x_position][cell.y_position] === undefined) {
      grid[cell.x_position][cell.y_position] = cell
    }
  })
  return grid
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
  germinatingCellPlantingPk?: number
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
  germinatingCellPlantingPk,
  onToggleGermination,
  onOpenMove,
  locationLabel
}) => {
  const totalGerminated = entries.reduce((sum, e) => sum + (germinatedByCellPlanting[e.cellPlantingPk] ?? 0), 0)

  return (
    <td style={{ textAlign: 'center', minWidth: 70, verticalAlign: 'top' }}>
      <div>{cell?.pk ?? ''}</div>
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
            variant={germinatingCellPlantingPk === entry.cellPlantingPk ? 'success' : 'outline-success'}
            style={{ fontSize: '0.75em', padding: '1px 4px', marginTop: 4 }}
            onClick={() => onToggleGermination(entry.cellPlantingPk)}
          >
            + Germination{entries.length > 1 ? ` (#${entry.cellPlantingPk})` : ''}
          </Button>
        ))}
    </td>
  )
}

type GerminationFormProps = {
  cellPlantingPk: number
  date: string
  notes: string
  onChangeDate: (value: string) => void
  onChangeNotes: (value: string) => void
  onSave: () => void
  onCancel: () => void
}

const GerminationForm: React.FC<GerminationFormProps> = ({ cellPlantingPk, date, notes, onChangeDate, onChangeNotes, onSave, onCancel }) => (
  <div style={{ marginTop: 16, padding: 12, border: '1px solid #ccc', maxWidth: 400 }}>
    <h5>Record Germination (cell planting #{cellPlantingPk})</h5>
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
      <Button variant="success" onClick={onSave} disabled={!date}>
        Save
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

function SeedTrayDetails({ seedTrayPk }: SeedTrayDetailsProps) {
  const cache = useQueryClient()
  const [germinatingCellPlantingPk, setGerminatingCellPlantingPk] = React.useState<number>()
  const [germinationDate, setGerminationDate] = React.useState(localDatetimeInputValue())
  const [germinationNotes, setGerminationNotes] = React.useState('')
  const [moveForm, setMoveForm] = React.useState<MoveForm>()
  const [inventoryAction, setInventoryAction] = React.useState<InventoryAction>()
  const [inventoryDestination, setInventoryDestination] = React.useState<number>()
  const [inventoryReason, setInventoryReason] = React.useState('')
  const [inventoryCost, setInventoryCost] = React.useState('0.0000')
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
    queryKey: queryKeys.inventory.locations,
    queryFn: ({ signal }) => getInventoryLocations(signal)
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
  const moveTrayPk = moveForm?.locationType === 'seed_tray_cell' ? moveForm.moveSeedTrayPk : undefined
  const moveCellsQuery = useQuery({
    queryKey: queryKeys.seedTrays.cells(moveTrayPk ?? 0),
    queryFn: ({ signal }) => getSeedTrayCells(moveTrayPk as number, signal),
    enabled: Boolean(moveTrayPk)
  })
  const germinationMutation = useMutation({
    mutationFn: addSpecificPlant,
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
  const seedTrayCells = buildCellGrid(seedTrayModel, allCells)
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
    if (!germinatingCellPlantingPk) return
    const parsedDate = parseLocalDatetimeInput(germinationDate)
    if (!parsedDate) return
    await germinationMutation.mutateAsync({
      cell_planting: germinatingCellPlantingPk,
      germinated: parsedDate.toISOString(),
      notes: germinationNotes || undefined
    })
    setGerminatingCellPlantingPk(undefined)
    setGerminationNotes('')
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
                    germinatingCellPlantingPk={germinatingCellPlantingPk}
                    onToggleGermination={(cellPlantingPk) => setGerminatingCellPlantingPk((currentPk) => (currentPk === cellPlantingPk ? undefined : cellPlantingPk))}
                    onOpenMove={openMoveForm}
                    locationLabel={locationLabel}
                  />
                ))}
              </tr>
            ))}
        </tbody>
      </Table>
      {germinatingCellPlantingPk && (
        <GerminationForm
          cellPlantingPk={germinatingCellPlantingPk}
          date={germinationDate}
          notes={germinationNotes}
          onChangeDate={setGerminationDate}
          onChangeNotes={setGerminationNotes}
          onSave={handleRecordGermination}
          onCancel={() => setGerminatingCellPlantingPk(undefined)}
        />
      )}
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
