import React from 'react'
import * as ReactDOM from 'react-dom/client'

import { SeedTray, SeedTrayCell, SeedTrayModel } from '../types/seedtrays'
import { getSeedTrayModels, getSeedTrays, getSeedTrayCells } from '../api/seedtrays'
import { Button, Table } from 'react-bootstrap'
import { SeedTrayPlanting, SpecificPlant, SpecificPlantLocation } from '../types/plantings'
import { getPlantingSeedTray, getSpecificPlantsBySeedTray, addSpecificPlant, addSpecificPlantLocation, endSpecificPlantLocation } from '../api/plantings'
import { SeedPacketDetails } from '../types/seeds'
import { getSeedPacketsCurrent } from '../api/seeds'
import { GardenSquare } from '../types/garden'
import { getGardenSquares } from '../api/garden'

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

type CellPlantingEntry = { cellPlantingPk: number; quantity: number; plantingPk: number }

interface SeedTrayDetailsState {
  seedTray?: SeedTray
  seedTrayModel?: SeedTrayModel
  seedTrayCells?: (SeedTrayCell | undefined)[][]
  allCells?: Array<SeedTrayCell>
  allSeedTrays?: Array<SeedTray>
  plantings?: Array<SeedTrayPlanting>
  seeds?: { [key: number]: SeedPacketDetails }
  specificPlants?: Array<SpecificPlant>
  gardenSquares?: Array<GardenSquare>
  germinatingCellPlantingPk?: number
  germinationDate: string
  germinationNotes: string
  moveForm?: MoveForm
  moveCells?: Array<SeedTrayCell>
  moveCellsLoading?: boolean
  // derived from specificPlants + plantings
  cellCurrentPlantMap: { [cellPk: number]: Array<SpecificPlant> }
  cellPlantingMap: { [cellPk: number]: Array<CellPlantingEntry> }
  germinatedByCellPlanting: { [cellPlantingPk: number]: number }
  cellTotals: { [cellPk: number]: number }
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
      <div style={{ fontWeight: 'bold' }}>{cell ? total || 0 : ''}</div>
      {cell && totalGerminated > 0 && <div style={{ color: 'green', fontSize: '0.85em' }}>{totalGerminated} germinated</div>}
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
                    {locationLabel(l)} {l.started}→{l.ended ?? 'now'}
                  </div>
                ))}
              </details>
            )}
          </div>
        )
      })}
      {cell &&
        entries.map((entry) => {
          const germinated = germinatedByCellPlanting[entry.cellPlantingPk] ?? 0
          if (germinated >= entry.quantity) return null
          return (
            <Button
              key={entry.cellPlantingPk}
              size="sm"
              variant={germinatingCellPlantingPk === entry.cellPlantingPk ? 'success' : 'outline-success'}
              style={{ fontSize: '0.75em', padding: '1px 4px', marginTop: 4 }}
              onClick={() => onToggleGermination(entry.cellPlantingPk)}
            >
              + Germination{entries.length > 1 ? ` (#${entry.cellPlantingPk})` : ''}
            </Button>
          )
        })}
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
        Date: <input type="date" value={date} onChange={(e) => onChangeDate(e.target.value)} />
      </label>
    </div>
    <div style={{ marginTop: 8 }}>
      <label>
        Notes: <input type="text" value={notes} onChange={(e) => onChangeNotes(e.target.value)} placeholder="Optional" />
      </label>
    </div>
    <div style={{ marginTop: 8 }}>
      <Button variant="success" onClick={onSave}>
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
                  Tray #{t.pk} ({t.created})
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
        Date: <input type="date" value={form.date} onChange={(e) => onChange({ ...form, date: e.target.value })} />
      </label>
    </div>
    <div style={{ marginTop: 8 }}>
      <label>
        Notes: <input type="text" value={form.notes} onChange={(e) => onChange({ ...form, notes: e.target.value })} placeholder="Optional" />
      </label>
    </div>
    <div style={{ marginTop: 8 }}>
      <Button variant="primary" onClick={onSave} disabled={form.locationType === 'garden_square' ? !form.gardenSquarePk : !form.seedTrayCellPk}>
        Save
      </Button>{' '}
      <Button variant="secondary" onClick={onCancel}>
        Cancel
      </Button>
    </div>
  </div>
)

class SeedTrayDetails extends React.Component<SeedTrayDetailsProps, SeedTrayDetailsState> {
  private _latestMoveTrayRequestPk: number | undefined = undefined

  constructor(props: SeedTrayDetailsProps) {
    super(props)
    this.state = {
      germinationDate: new Date().toISOString().slice(0, 10),
      germinationNotes: '',
      cellCurrentPlantMap: {},
      cellPlantingMap: {},
      germinatedByCellPlanting: {},
      cellTotals: {}
    }
  }

  async componentDidMount() {
    const [seedTrayModels, seedTrays, seedTrayCells, seedTrayPlantings, seedPacketDetails, specificPlants, gardenSquares] = await Promise.all([
      getSeedTrayModels(),
      getSeedTrays(),
      getSeedTrayCells(this.props.seedTrayPk),
      getPlantingSeedTray(this.props.seedTrayPk),
      getSeedPacketsCurrent(),
      getSpecificPlantsBySeedTray(this.props.seedTrayPk),
      getGardenSquares()
    ])
    const seedTray = seedTrays.find((t) => t.pk === this.props.seedTrayPk)
    const seedTrayModel = seedTrayModels.find((m) => m.pk === seedTray?.model)
    this.setState({
      seedTray,
      seedTrayModel,
      plantings: seedTrayPlantings,
      seedTrayCells: buildCellGrid(seedTrayModel, seedTrayCells),
      allCells: seedTrayCells,
      seeds: seedPacketDetails.reduce((acc: { [key: number]: SeedPacketDetails }, seed) => {
        acc[seed.pk] = seed
        return acc
      }, {}),
      specificPlants,
      gardenSquares,
      allSeedTrays: seedTrays,
      ...computeCellData(specificPlants, seedTrayPlantings)
    })
  }

  async refreshPlants() {
    const specificPlants = await getSpecificPlantsBySeedTray(this.props.seedTrayPk)
    this.setState((prev) => ({ specificPlants, ...computeCellData(specificPlants, prev.plantings) }))
  }

  async handleRecordGermination() {
    const { germinatingCellPlantingPk, germinationDate, germinationNotes } = this.state
    if (!germinatingCellPlantingPk) return
    await addSpecificPlant({
      cell_planting: germinatingCellPlantingPk,
      germinated: germinationDate,
      notes: germinationNotes || undefined
    })
    await this.refreshPlants()
    this.setState({ germinatingCellPlantingPk: undefined, germinationNotes: '' })
  }

  async handleRecordMove() {
    const { moveForm } = this.state
    if (!moveForm) return
    if (moveForm.currentLocationPk) {
      await endSpecificPlantLocation(moveForm.currentLocationPk, moveForm.date)
    }
    await addSpecificPlantLocation({
      specific_plant: moveForm.plantPk,
      location_type: moveForm.locationType,
      seed_tray_cell: moveForm.locationType === 'seed_tray_cell' ? moveForm.seedTrayCellPk : undefined,
      garden_square: moveForm.locationType === 'garden_square' ? moveForm.gardenSquarePk : undefined,
      started: moveForm.date,
      notes: moveForm.notes || undefined
    })
    await this.refreshPlants()
    this.setState({ moveForm: undefined })
  }

  openMoveForm(plant: SpecificPlant) {
    const current = currentLocation(plant)
    this.setState({
      moveForm: {
        plantPk: plant.pk,
        currentLocationPk: current?.pk,
        locationType: 'garden_square',
        gardenSquarePk: undefined,
        date: new Date().toISOString().slice(0, 10),
        notes: ''
      },
      moveCells: undefined,
      moveCellsLoading: false
    })
  }

  async handleMoveTrayChange(rawValue: string) {
    if (!rawValue) {
      this._latestMoveTrayRequestPk = undefined
      this.setState((prev) => {
        if (!prev.moveForm || prev.moveForm.locationType !== 'seed_tray_cell') return null
        return { moveForm: { ...prev.moveForm, moveSeedTrayPk: undefined, seedTrayCellPk: undefined }, moveCells: undefined, moveCellsLoading: false }
      })
      return
    }
    const trayPk = Number(rawValue)
    this._latestMoveTrayRequestPk = trayPk
    this.setState((prev) => {
      if (!prev.moveForm || prev.moveForm.locationType !== 'seed_tray_cell') return null
      return { moveForm: { ...prev.moveForm, moveSeedTrayPk: trayPk, seedTrayCellPk: undefined }, moveCells: undefined, moveCellsLoading: true }
    })
    const cells = await getSeedTrayCells(trayPk)
    if (this._latestMoveTrayRequestPk === trayPk) {
      this.setState({ moveCells: cells, moveCellsLoading: false })
    }
  }

  locationLabel(loc: SpecificPlantLocation): string {
    if (loc.location_type === 'seed_tray_cell') {
      const cell = this.state.allCells?.find((c) => c.pk === loc.seed_tray_cell)
      return cell ? `Cell (${cell.x_position},${cell.y_position})` : `Cell #${loc.seed_tray_cell}`
    }
    const sq = this.state.gardenSquares?.find((s) => s.pk === loc.garden_square)
    return sq ? sq.name : `Square #${loc.garden_square}`
  }

  render() {
    const { seedTray, seedTrayModel, seedTrayCells, germinatingCellPlantingPk, moveForm, cellCurrentPlantMap, cellPlantingMap, germinatedByCellPlanting, cellTotals } = this.state
    if (!seedTray) {
      return <div>Loading...</div>
    }

    return (
      <div>
        <h1>Seed Tray Details (ID: {seedTray.pk})</h1>
        <p>
          Model: {seedTrayModel?.identifier} ({seedTrayModel?.description})
        </p>
        <p>Created: {seedTray.created}</p>
        <p>Notes: {seedTray.notes}</p>
        Plantings:
        <Table border={1} cellPadding={5} cellSpacing={0}>
          <thead>
            <tr>
              <th>Planting ID</th>
              <th>Planted On</th>
              <th>Quantity</th>
              <th>Seeds Used</th>
              <th>Notes</th>
              <th>Removed</th>
            </tr>
          </thead>
          <tbody>
            {this.state.plantings?.map((planting) => {
              const seeds = this.state.seeds?.[planting.seeds_used]
              return (
                <tr key={planting.pk}>
                  <td>{planting.pk}</td>
                  <td>{planting.planted}</td>
                  <td>{planting.quantity}</td>
                  <td>
                    {seeds?.plant} - {seeds?.variety}
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
              seedTrayCells?.map((row, r) => (
                <tr key={r}>
                  {row.map((cell, c) => (
                    <SeedTrayCellView
                      key={c}
                      cell={cell}
                      total={cell ? cellTotals[cell.pk] || 0 : 0}
                      entries={cell ? (cellPlantingMap[cell.pk] ?? []) : []}
                      plants={cell ? (cellCurrentPlantMap[cell.pk] ?? []) : []}
                      germinatedByCellPlanting={germinatedByCellPlanting}
                      germinatingCellPlantingPk={germinatingCellPlantingPk}
                      onToggleGermination={(pk) =>
                        this.setState((prev) => ({
                          germinatingCellPlantingPk: prev.germinatingCellPlantingPk === pk ? undefined : pk
                        }))
                      }
                      onOpenMove={(plant) => this.openMoveForm(plant)}
                      locationLabel={(loc) => this.locationLabel(loc)}
                    />
                  ))}
                </tr>
              ))}
          </tbody>
        </Table>
        {germinatingCellPlantingPk && (
          <GerminationForm
            cellPlantingPk={germinatingCellPlantingPk}
            date={this.state.germinationDate}
            notes={this.state.germinationNotes}
            onChangeDate={(value) => this.setState({ germinationDate: value })}
            onChangeNotes={(value) => this.setState({ germinationNotes: value })}
            onSave={() => this.handleRecordGermination()}
            onCancel={() => this.setState({ germinatingCellPlantingPk: undefined })}
          />
        )}
        {moveForm && (
          <MovePlantForm
            form={moveForm}
            gardenSquares={this.state.gardenSquares}
            allSeedTrays={this.state.allSeedTrays}
            moveCells={this.state.moveCells}
            moveCellsLoading={this.state.moveCellsLoading}
            onChange={(form) => this.setState({ moveForm: form })}
            onChangeTray={(value) => this.handleMoveTrayChange(value)}
            onSave={() => this.handleRecordMove()}
            onCancel={() => this.setState({ moveForm: undefined })}
          />
        )}
      </div>
    )
  }
}

function showSeedTrayDetails(elem: string, seedTrayPk: number) {
  const root = ReactDOM.createRoot(document.getElementById(elem) as HTMLElement)
  root.render(<SeedTrayDetails seedTrayPk={seedTrayPk} />)
}

;(globalThis as Record<string, unknown>).showSeedTrayDetails = showSeedTrayDetails
