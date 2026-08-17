import 'bootstrap'
import 'bootstrap/dist/css/bootstrap.css'

import React from 'react'
import { Table, Button } from 'react-bootstrap'
import Select from 'react-select'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router'

import { Supplier } from './types/suppliers'
import { PlantVariety } from './types/plants'
import { Seed, SeedPacket } from './types/seeds'
import { GardenBed, GardenSquare } from './types/garden'
import { GardenSquareDirectPlantingCreate, GardenSquarePlanting, SeedTrayPlantingCreate, SeedTrayPlantingDetails, SowingCorrection } from './types/plantings'
import { SeedTray, SeedTrayModel } from './types/seedtrays'
import { SelectOption } from './types/others'
import { getGardenAreas, getGardenBeds, getGardenSquares } from './api/garden'
import { formatDate, formatDateRange, formatQuantity } from './utils'
import {
  getPlantingSeedTrayCurrent,
  getPlantingGardenSquaresCurrent,
  addPlantingDirectSowGardenSquare,
  addPlantingSeedTray,
  completePlantingDirectSowGardenSquare,
  completePlantingTransplantedGardenSquare,
  completePlantingSeedTray,
  correctGardenSquareSowing,
  correctSeedTraySowing,
  postSpecificPlantOutcome
} from './api/plantings'
import { getPlantVarieties } from './api/plants'
import { getSeedPackets, getSeeds } from './api/seeds'
import { getSeedTrayModels, getSeedTrays, getSeedTrayCells } from './api/seedtrays'
import { SeedTrayCell } from './types/seedtrays'
import { getSuppliers } from './api/supplies'
import { BatchChooser, isChoiceComplete, type BatchChoice } from './plantings/batch_chooser'
import { queryKeys } from './query'
import { buildSeedTrayCellGrid } from './seedtray/grid'

function packetVarietyPk(packetPk: number | undefined, packets: Array<SeedPacket>, seeds: Array<Seed>): number | undefined {
  if (packetPk === undefined) {
    return undefined
  }
  const packet = packets.find((candidate) => candidate.pk === packetPk)
  return seeds.find((candidate) => candidate.pk === packet?.seeds)?.plant_variety
}

function packetBalanceLabel(packet: SeedPacket): string {
  const inventory = packet.inventory
  if (!inventory || inventory.remaining_quantity === null) {
    return `quantity unknown; ${formatQuantity(inventory?.sown_quantity, '0')} sown`
  }
  return `${formatQuantity(inventory.remaining_quantity)} ${inventory.base_unit} remaining (${inventory.quantity_certainty})`
}

// The codes a gardener can read off the packet in their hand. They are searchable
// but kept out of the label, which is already a long line.
interface PacketOption extends SelectOption {
  codes: string
  searchText: string
}

function byPk<T extends { pk: number }>(records: Array<T>): Map<number, T> {
  return new Map(records.map((record) => [record.pk, record]))
}

// Index once per call rather than scanning three arrays for every packet: the
// picker is rebuilt whenever the table renders, and every seed tray row shares it.
function packetSelectOptions(packets: Array<SeedPacket>, seeds: Array<Seed>, suppliers: Array<Supplier>, varieties: Array<PlantVariety>): Array<PacketOption> {
  const seedsByPk = byPk(seeds)
  const suppliersByPk = byPk(suppliers)
  const varietiesByPk = byPk(varieties)
  return packets.map((packet) => {
    const seedData = seedsByPk.get(packet.seeds)
    const supplier = seedData === undefined ? undefined : suppliersByPk.get(seedData.supplier)
    const variety = seedData === undefined ? undefined : varietiesByPk.get(seedData.plant_variety)
    const label = `${variety?.name} from ${supplier?.name} (Sow by: ${packet.sow_by || 'unknown'}; ${packetBalanceLabel(packet)})`
    // supplier_lot_reference is blank on plenty of packets, and inventory is null
    // for one with no stock lot, so drop the empties rather than print separators
    // around nothing.
    const codes = [seedData?.supplier_code, packet.inventory?.supplier_lot_reference, packet.inventory?.lot_identifier].filter(Boolean).join(' · ')
    return { value: packet.pk, label, codes, searchText: `${label} ${codes}`.toLowerCase() }
  })
}

function formatPacketOption(option: PacketOption, meta: { context: string }) {
  // formatOptionLabel also renders the chosen value inside the closed control,
  // where a second line would stretch the table row. Codes belong in the menu.
  if (meta.context === 'value' || !option.codes) {
    return option.label
  }
  return (
    <div>
      <div>{option.label}</div>
      <div style={{ fontSize: '0.85em', color: '#6c757d' }}>{option.codes}</div>
    </div>
  )
}

// react-select's default filter only reads the label, so the codes need this to
// be searchable at all.
function filterPacketOption(option: { data: PacketOption }, input: string) {
  return option.data.searchText.includes(input.toLowerCase())
}

// react-select reports a cleared control as null and an empty option value as
// undefined; both mean "nothing chosen" to every picker in this file.
function selectOptionToPk(option: SelectOption | null): number | undefined {
  const value = option?.value
  if (value === undefined || value === null) {
    return undefined
  }
  return Number(value)
}

interface SeedTrayCellGridProps {
  cells: Array<SeedTrayCell>
  model: SeedTrayModel
  cellQuantities: { [cellPk: number]: number }
  onToggleCell: (cellPk: number) => void
}

class SeedTrayCellGrid extends React.PureComponent<SeedTrayCellGridProps> {
  render() {
    const { cells, model, cellQuantities, onToggleCell } = this.props
    const cellGrid = buildSeedTrayCellGrid(model, cells)
    const cellGridRows = cellGrid.map((rowCells, rowIndex) => (
      <tr key={rowIndex}>
        {rowCells.map((cell, columnIndex) => (
          <td key={cell?.pk ?? `empty-${rowIndex}-${columnIndex}`} style={{ padding: '4px', border: '1px solid #ccc', textAlign: 'center' }}>
            {cell && (
              <Button
                type="button"
                variant={cellQuantities[cell.pk] ? 'primary' : 'outline-secondary'}
                aria-pressed={Boolean(cellQuantities[cell.pk])}
                onClick={() => onToggleCell(cell.pk)}
                style={{ minWidth: '76px', minHeight: '64px' }}
              >
                <div style={{ fontWeight: 'bold' }}>
                  Cell {cell.x_position}, {cell.y_position}
                </div>
                <div>{cellQuantities[cell.pk] ? `${cellQuantities[cell.pk]} seed${cellQuantities[cell.pk] === 1 ? '' : 's'}` : 'Tap to select'}</div>
              </Button>
            )}
          </td>
        ))}
      </tr>
    ))

    const cellTotal = Object.values(cellQuantities).reduce((sum, qty) => sum + qty, 0)

    return (
      <div style={{ marginBottom: '8px' }}>
        <div style={{ fontWeight: 'bold', marginBottom: '8px' }}>{cellTotal} seeds selected</div>
        <Table border={1} cellPadding={5} cellSpacing={0} style={{ width: 'auto' }}>
          <tbody>{cellGridRows}</tbody>
        </Table>
      </div>
    )
  }
}

interface NewSeedTrayPlantingRowProps {
  seeds: Array<Seed>
  seedPackets: Array<SeedPacket>
  packetOptions: Array<PacketOption>
  seedTrays: Array<SeedTray>
  seedTrayModels: { [key: number]: SeedTrayModel }
  done: () => void
  createPlanting: (data: SeedTrayPlantingCreate) => Promise<void>
}

function NewSeedTrayPlantingRow({ seeds, seedPackets, packetOptions, seedTrays, seedTrayModels, done, createPlanting }: NewSeedTrayPlantingRowProps) {
  const [seedPacket, setSeedPacket] = React.useState<number>()
  const [quantity, setQuantity] = React.useState(1)
  const [seedTray, setSeedTray] = React.useState<number>()
  const [location, setLocation] = React.useState<string>()
  const [notes, setNotes] = React.useState<string>()
  const [cellQuantities, setCellQuantities] = React.useState<Record<number, number>>({})
  const [seedsPerCell, setSeedsPerCell] = React.useState(1)
  const [batchChoice, setBatchChoice] = React.useState<BatchChoice>({})
  const [error, setError] = React.useState<string>()
  const packetVariety = packetVarietyPk(seedPacket, seedPackets, seeds)
  const { data: seedTrayCells = [] } = useQuery({
    queryKey: queryKeys.seedTrays.cells(seedTray ?? 0),
    queryFn: ({ signal }) => getSeedTrayCells(seedTray as number, signal),
    enabled: Boolean(seedTray)
  })

  function updateSeedPacket(selectedSeedPacket: SelectOption | null) {
    setBatchChoice({})
    setSeedPacket(selectOptionToPk(selectedSeedPacket))
  }

  function updateQuantity(event: React.ChangeEvent<HTMLInputElement>) {
    const { value } = event.target

    if (value === '' || value === undefined || value === null) {
      setQuantity(0)
      return
    }
    setQuantity(Number(value))
  }

  function updateSeedTray(selectedSeedTray: SelectOption | null) {
    const trayPk = selectOptionToPk(selectedSeedTray)
    setCellQuantities({})
    setQuantity(trayPk === undefined ? 1 : 0)
    setSeedTray(trayPk)
  }

  function updateSeedsPerCell(event: React.ChangeEvent<HTMLInputElement>) {
    const nextSeedsPerCell = Math.max(1, parseInt(event.target.value, 10) || 1)
    const updated = Object.fromEntries(Object.keys(cellQuantities).map((cellPk) => [cellPk, nextSeedsPerCell]))
    setSeedsPerCell(nextSeedsPerCell)
    setCellQuantities(updated)
    setQuantity(Object.keys(updated).length * nextSeedsPerCell)
  }

  function toggleCell(cellPk: number) {
    const updated = { ...cellQuantities }
    if (updated[cellPk]) {
      delete updated[cellPk]
    } else {
      updated[cellPk] = seedsPerCell
    }
    setCellQuantities(updated)
    setQuantity(Object.values(updated).reduce((sum, cellQuantity) => sum + cellQuantity, 0))
  }

  async function add() {
    if (seedPacket === undefined) {
      return
    }
    if (!isChoiceComplete(batchChoice)) {
      setError('Choose an existing batch or name a new one')
      return
    }

    const cellPlantings = Object.entries(cellQuantities).map(([cellPk, qty]) => ({
      cell: Number(cellPk),
      quantity: qty
    }))

    // Validate per-cell total doesn't exceed overall quantity
    const cellTotal = Object.values(cellQuantities).reduce((sum, qty) => sum + qty, 0)
    if (cellTotal > quantity) {
      setError(`Cell total (${cellTotal}) exceeds planting quantity (${quantity})`)
      return
    }

    setError(undefined)

    const data: SeedTrayPlantingCreate = {
      seeds_used: seedPacket,
      ...batchChoice,
      quantity,
      location,
      seed_tray: seedTray,
      notes
    }
    if (cellPlantings.length > 0) {
      data.cell_plantings = cellPlantings
    }
    await createPlanting(data)
    done()
  }

  const trayOptions = seedTrays.map((tray) => ({ value: tray.pk, label: `${tray.pk} (${seedTrayModels[tray.model]?.description})` }))
  const selectedSeedTrayModel = seedTray === undefined ? undefined : seedTrayModels[seedTrays.find((tray) => tray.pk === seedTray)?.model ?? 0]
  const hasSelectedCells = Object.keys(cellQuantities).length > 0

  return (
    <>
      {error && (
        <tr>
          <td colSpan={8} style={{ padding: '8px', backgroundColor: '#f8d7da', color: '#721c24', border: '1px solid #f5c6cb' }}>
            <strong>Error:</strong> {error}
          </td>
        </tr>
      )}
      <tr>
        <td>
          <Select
            onChange={updateSeedPacket}
            options={packetOptions}
            value={packetOptions.find((option) => option.value === seedPacket) ?? null}
            formatOptionLabel={formatPacketOption}
            filterOption={filterPacketOption}
            isClearable
          />
        </td>
        <td>
          <BatchChooser variety={packetVariety} value={batchChoice} onChange={setBatchChoice} />
        </td>
        <td>
          <input type="number" min="0" value={quantity} readOnly={hasSelectedCells} onChange={updateQuantity} />
        </td>
        <td></td>
        <td>
          <Select onChange={updateSeedTray} options={trayOptions} value={trayOptions.find((option) => option.value === seedTray)} />
        </td>
        <td>
          <input type="text" onChange={(event) => setLocation(event.target.value)} />
        </td>
        <td>
          <textarea onChange={(event) => setNotes(event.target.value)} />
        </td>
        <td>
          <Button onClick={add}>Add</Button>
          <Button onClick={done}>Cancel</Button>
        </td>
      </tr>
      {seedTray !== undefined && selectedSeedTrayModel && seedTrayCells.length > 0 && (
        <tr>
          <td colSpan={8} style={{ padding: '16px' }}>
            <label style={{ display: 'block', marginBottom: '8px', fontWeight: 'bold' }}>
              Seeds per selected cell: <input type="number" min="1" value={seedsPerCell} onChange={updateSeedsPerCell} style={{ width: '70px' }} />
            </label>
            <SeedTrayCellGrid cells={seedTrayCells} model={selectedSeedTrayModel} cellQuantities={cellQuantities} onToggleCell={toggleCell} />
          </td>
        </tr>
      )}
    </>
  )
}

interface SeedTrayPlantingRowProps {
  planting: SeedTrayPlantingDetails
  packetOptions: Array<PacketOption>
  completePlanting: (plantingPk: number) => Promise<void>
  correctPlanting: (plantingPk: number, data: SowingCorrection) => Promise<void>
}

function SeedTrayPlantingRow({ planting, packetOptions, completePlanting, correctPlanting }: SeedTrayPlantingRowProps) {
  const [correcting, setCorrecting] = React.useState(false)
  const [packetPk, setPacketPk] = React.useState(planting.seeds_used)
  const [quantity, setQuantity] = React.useState(planting.quantity)
  const [reason, setReason] = React.useState('')

  // A correction always replaces one packet with another, so there is no empty
  // state to fall back to: backspace clearing the box must keep the current pick
  // rather than post seeds_used: NaN.
  function updatePacketPk(option: SelectOption | null) {
    const nextPacketPk = selectOptionToPk(option)
    if (nextPacketPk === undefined) {
      return
    }
    setPacketPk(nextPacketPk)
  }

  async function correct() {
    await correctPlanting(planting.pk, { seeds_used: packetPk, quantity, reason })
    setCorrecting(false)
    setReason('')
  }

  return (
    <tr>
      <td>
        {planting.plant} - {planting.variety}
      </td>
      <td>
        <Link to={`/plantings/batches/${planting.batch}`}>{planting.batch_code}</Link>
      </td>
      <td>
        <span title="Number of seeds or seed clusters sown">Sown: {planting.quantity}</span> (
        <span title="Number of individual plants that have germinated">Germinated: {planting.germinated_count}</span>,{' '}
        <span title="Number that have been transplanted to a garden square">Transplanted: {planting.transplanted_count}</span>)
      </td>
      <td>{formatDate(planting.planted)}</td>
      <td>{planting.seed_tray}</td>
      <td>{planting.location}</td>
      <td>{formatDateRange(planting.germination_date_early, planting.germination_date_late)}</td>
      <td>{planting.notes}</td>
      <td>
        {planting.seed_tray && (
          <Link className="btn btn-primary" to={`/seedtrays/${planting.seed_tray}`}>
            Manage Plants
          </Link>
        )}
        <Button onClick={() => completePlanting(planting.pk)}>Close sowing</Button>
        <Button variant="outline-secondary" onClick={() => setCorrecting((current) => !current)}>
          Correct sowing
        </Button>
        {correcting && (
          <div>
            <div style={{ minWidth: '280px' }}>
              <Select
                onChange={updatePacketPk}
                options={packetOptions}
                value={packetOptions.find((option) => option.value === packetPk) ?? null}
                formatOptionLabel={formatPacketOption}
                filterOption={filterPacketOption}
              />
            </div>
            <input type="number" min="1" value={quantity} onChange={(event) => setQuantity(Number(event.target.value))} />
            <input required placeholder="Correction reason" value={reason} onChange={(event) => setReason(event.target.value)} />
            <Button size="sm" disabled={!reason} onClick={correct}>
              Apply correction
            </Button>
          </div>
        )}
      </td>
    </tr>
  )
}

function SeedTrayPlantingTable() {
  const queryClient = useQueryClient()
  const [showPlantingAdd, setShowPlantingAdd] = React.useState(false)
  const { data: suppliers = [] } = useQuery({
    queryKey: queryKeys.suppliers.all,
    queryFn: ({ signal }) => getSuppliers(signal)
  })
  const { data: varieties = [] } = useQuery({
    queryKey: queryKeys.plants.varieties,
    queryFn: ({ signal }) => getPlantVarieties(signal)
  })
  const { data: seeds = [] } = useQuery({
    queryKey: queryKeys.seeds.catalog,
    queryFn: ({ signal }) => getSeeds(signal)
  })
  const { data: seedPackets = [] } = useQuery({
    queryKey: queryKeys.seeds.packets.raw,
    queryFn: ({ signal }) => getSeedPackets(signal)
  })
  const { data: plantings = [] } = useQuery({
    queryKey: queryKeys.plantings.currentSeedTrays,
    queryFn: ({ signal }) => getPlantingSeedTrayCurrent(signal)
  })
  const { data: seedTrays = [] } = useQuery({
    queryKey: queryKeys.seedTrays.trays,
    queryFn: ({ signal }) => getSeedTrays(signal)
  })
  const { data: seedTrayModelList = [] } = useQuery({
    queryKey: queryKeys.seedTrays.models,
    queryFn: ({ signal }) => getSeedTrayModels(signal)
  })
  const createMutation = useMutation({
    mutationFn: addPlantingSeedTray,
    onSuccess: () => Promise.all([queryClient.invalidateQueries({ queryKey: queryKeys.plantings.all }), queryClient.invalidateQueries({ queryKey: queryKeys.seeds.packets.all })])
  })
  const completeMutation = useMutation({
    mutationFn: completePlantingSeedTray,
    onSuccess: () => Promise.all([queryClient.invalidateQueries({ queryKey: queryKeys.plantings.all }), queryClient.invalidateQueries({ queryKey: queryKeys.seeds.packets.all })])
  })
  const correctionMutation = useMutation({
    mutationFn: ({ pk, data }: { pk: number; data: SowingCorrection }) => correctSeedTraySowing(pk, data),
    onSuccess: () => Promise.all([queryClient.invalidateQueries({ queryKey: queryKeys.plantings.all }), queryClient.invalidateQueries({ queryKey: queryKeys.seeds.packets.all })])
  })
  const seedTrayModels = seedTrayModelList.reduce<Record<number, SeedTrayModel>>((models, model) => {
    models[model.pk] = model
    return models
  }, {})

  async function createPlanting(data: SeedTrayPlantingCreate) {
    await createMutation.mutateAsync(data)
  }

  async function completePlanting(plantingPk: number) {
    await completeMutation.mutateAsync(plantingPk)
  }

  async function correctPlanting(plantingPk: number, data: SowingCorrection) {
    await correctionMutation.mutateAsync({ pk: plantingPk, data })
  }

  // Every row's correction picker offers the same packets, so build the options
  // once here rather than once per row, and only when the source data changes.
  const packetOptions = React.useMemo(() => packetSelectOptions(seedPackets, seeds, suppliers, varieties), [seedPackets, seeds, suppliers, varieties])

  const rows = []
  if (showPlantingAdd) {
    rows.push(
      <NewSeedTrayPlantingRow
        key="new"
        seedPackets={seedPackets}
        seeds={seeds}
        packetOptions={packetOptions}
        seedTrays={seedTrays}
        seedTrayModels={seedTrayModels}
        createPlanting={createPlanting}
        done={() => setShowPlantingAdd(false)}
      />
    )
  }
  for (const planting of plantings) {
    rows.push(<SeedTrayPlantingRow key={planting.pk} planting={planting} packetOptions={packetOptions} completePlanting={completePlanting} correctPlanting={correctPlanting} />)
  }
  return (
    <Table>
      <thead>
        <tr>
          <td>
            Plant{' '}
            <Button variant="link" className="p-0 align-baseline" aria-label="Add seed tray planting" onClick={() => setShowPlantingAdd(true)}>
              +
            </Button>
          </td>
          <td>Batch</td>
          <td>Seeds / clusters sown</td>
          <td>Date</td>
          <td>Seed Tray</td>
          <td>Location</td>
          <td>Expected Germination</td>
          <td>Notes</td>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </Table>
  )
}

interface NewGardenSquarePlantingRowProps {
  seeds: Array<Seed>
  seedPackets: Array<SeedPacket>
  packetOptions: Array<PacketOption>
  gardenBeds: Array<GardenBed>
  gardenSquares: Array<GardenSquare>
  done: () => void
  createPlanting: (data: GardenSquareDirectPlantingCreate) => Promise<void>
}

function NewGardenSquarePlantingRow({ seeds, seedPackets, packetOptions, gardenBeds, gardenSquares, done, createPlanting }: NewGardenSquarePlantingRowProps) {
  const [seedPacket, setSeedPacket] = React.useState<number>()
  const [quantity, setQuantity] = React.useState(1)
  const [location, setLocation] = React.useState<number>()
  const [notes, setNotes] = React.useState<string>()
  const [batchChoice, setBatchChoice] = React.useState<BatchChoice>({})
  const [error, setError] = React.useState<string>()
  const packetVariety = packetVarietyPk(seedPacket, seedPackets, seeds)

  function updateSeedPacket(selectedSeedPacket: SelectOption | null) {
    setBatchChoice({})
    setSeedPacket(selectOptionToPk(selectedSeedPacket))
  }

  function updateQuantity(event: React.ChangeEvent<HTMLInputElement>) {
    const { value } = event.target
    setQuantity(value === '' ? 0 : Number(value))
  }

  function updateLocation(selectedLocation: SelectOption | null) {
    setLocation(selectOptionToPk(selectedLocation))
  }

  async function add() {
    if (seedPacket === undefined || location === undefined) {
      return
    }
    if (!isChoiceComplete(batchChoice)) {
      setError('Choose an existing batch or name a new one')
      return
    }
    setError(undefined)
    const data: GardenSquareDirectPlantingCreate = {
      seeds_used: seedPacket,
      ...batchChoice,
      quantity,
      location,
      notes
    }
    await createPlanting(data)
    done()
  }

  const locations = gardenBeds.flatMap((gardenBedData) =>
    gardenSquares
      .filter((square) => square.bed === gardenBedData.pk)
      .map((gardenSquareData) => ({
        value: gardenSquareData.pk,
        label: `${gardenBedData.name} - ${gardenSquareData.name}`
      }))
  )

  return (
    <>
      {error && (
        <tr>
          <td colSpan={8} style={{ padding: '8px', backgroundColor: '#f8d7da', color: '#721c24', border: '1px solid #f5c6cb' }}>
            <strong>Error:</strong> {error}
          </td>
        </tr>
      )}
      <tr>
        <td>
          <Select
            onChange={updateSeedPacket}
            options={packetOptions}
            value={packetOptions.find((option) => option.value === seedPacket) ?? null}
            formatOptionLabel={formatPacketOption}
            filterOption={filterPacketOption}
          />
        </td>
        <td>
          <BatchChooser variety={packetVariety} value={batchChoice} onChange={setBatchChoice} />
        </td>
        <td>
          <input type="number" defaultValue={quantity} onChange={updateQuantity} />
        </td>
        <td></td>
        <td>
          <Select onChange={updateLocation} options={locations} value={locations.find((option) => option.value === location)} />
        </td>
        <td>
          <textarea onChange={(event) => setNotes(event.target.value)} />
        </td>
        <td>
          <Button onClick={add}>Add</Button>
          <Button onClick={done}>Cancel</Button>
        </td>
      </tr>
    </>
  )
}

interface GardenSquarePlantingRowProps {
  planting: GardenSquarePlanting
  seedPackets: Array<SeedPacket>
  completePlanting: (planting: GardenSquarePlanting) => Promise<void>
  correctPlanting: (plantingPk: number, data: SowingCorrection) => Promise<void>
}

function GardenSquarePlantingRow({ planting, seedPackets, completePlanting, correctPlanting }: GardenSquarePlantingRowProps) {
  const [correcting, setCorrecting] = React.useState(false)
  const [packetPk, setPacketPk] = React.useState(planting.seeds_used)
  const [quantity, setQuantity] = React.useState(planting.quantity)
  const [reason, setReason] = React.useState('')
  const planted = planting.transplanted ? `${formatDate(planting.transplanted)} (S: ${formatDate(planting.planted)})` : formatDate(planting.planted)
  const directSowing = !planting.transplanted && planting.specific_plant_pk === undefined

  async function correct() {
    await correctPlanting(planting.planting_pk, { seeds_used: packetPk, quantity, reason })
    setCorrecting(false)
    setReason('')
  }

  return (
    <tr>
      <td>
        {planting.plant} - {planting.variety}
      </td>
      <td>
        <Link to={`/plantings/batches/${planting.batch}`}>{planting.batch_code}</Link>
      </td>
      <td>{planting.quantity}</td>
      <td>{planted}</td>
      <td>
        {planting.location.area} - {planting.location.bed} - {planting.location.name}
      </td>
      <td>{formatDateRange(planting.germination_date_early, planting.germination_date_late)}</td>
      <td>{formatDateRange(planting.maturity_date_early, planting.maturity_date_late)}</td>
      <td>{planting.notes}</td>
      <td>
        <Button onClick={() => completePlanting(planting)}>{planting.specific_plant_pk ? 'Harvested' : 'Close sowing'}</Button>
        {directSowing && (
          <Button variant="outline-secondary" onClick={() => setCorrecting((current) => !current)}>
            Correct sowing
          </Button>
        )}
        {correcting && (
          <div>
            <select value={packetPk} onChange={(event) => setPacketPk(Number(event.target.value))}>
              {seedPackets.map((packet) => (
                <option key={packet.pk} value={packet.pk}>
                  Packet #{packet.pk}: {packetBalanceLabel(packet)}
                </option>
              ))}
            </select>
            <input type="number" min="1" value={quantity} onChange={(event) => setQuantity(Number(event.target.value))} />
            <input required placeholder="Correction reason" value={reason} onChange={(event) => setReason(event.target.value)} />
            <Button size="sm" disabled={!reason} onClick={correct}>
              Apply correction
            </Button>
          </div>
        )}
      </td>
    </tr>
  )
}

function GardenSquarePlantingTable() {
  const queryClient = useQueryClient()
  const [showPlantingAdd, setShowPlantingAdd] = React.useState(false)
  const [filterGardenArea, setFilterGardenArea] = React.useState<number>()
  const [filterGardenBed, setFilterGardenBed] = React.useState<string>()
  const { data: suppliers = [] } = useQuery({
    queryKey: queryKeys.suppliers.all,
    queryFn: ({ signal }) => getSuppliers(signal)
  })
  const { data: varieties = [] } = useQuery({
    queryKey: queryKeys.plants.varieties,
    queryFn: ({ signal }) => getPlantVarieties(signal)
  })
  const { data: seeds = [] } = useQuery({
    queryKey: queryKeys.seeds.catalog,
    queryFn: ({ signal }) => getSeeds(signal)
  })
  const { data: seedPackets = [] } = useQuery({
    queryKey: queryKeys.seeds.packets.raw,
    queryFn: ({ signal }) => getSeedPackets(signal)
  })
  const { data: plantings = [] } = useQuery({
    queryKey: queryKeys.plantings.currentGardenSquares,
    queryFn: ({ signal }) => getPlantingGardenSquaresCurrent(signal)
  })
  const { data: gardenAreas = [] } = useQuery({
    queryKey: queryKeys.garden.areas,
    queryFn: ({ signal }) => getGardenAreas(signal)
  })
  const { data: gardenSquares = [] } = useQuery({
    queryKey: queryKeys.garden.squares,
    queryFn: ({ signal }) => getGardenSquares(signal)
  })
  const { data: gardenBeds = [] } = useQuery({
    queryKey: queryKeys.garden.beds,
    queryFn: ({ signal }) => getGardenBeds(signal)
  })
  const invalidatePlantingsAndPackets = () =>
    Promise.all([queryClient.invalidateQueries({ queryKey: queryKeys.plantings.all }), queryClient.invalidateQueries({ queryKey: queryKeys.seeds.packets.all })])
  const createMutation = useMutation({
    mutationFn: addPlantingDirectSowGardenSquare,
    onSuccess: invalidatePlantingsAndPackets
  })
  const completeDirectMutation = useMutation({
    mutationFn: completePlantingDirectSowGardenSquare,
    onSuccess: invalidatePlantingsAndPackets
  })
  const completeTransplantMutation = useMutation({
    mutationFn: completePlantingTransplantedGardenSquare,
    onSuccess: invalidatePlantingsAndPackets
  })
  const correctionMutation = useMutation({
    mutationFn: ({ pk, data }: { pk: number; data: SowingCorrection }) => correctGardenSquareSowing(pk, data),
    onSuccess: invalidatePlantingsAndPackets
  })
  const finishHarvestMutation = useMutation({
    mutationFn: (plantPk: number) => postSpecificPlantOutcome(plantPk, 'finish-harvest'),
    onSuccess: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.plantings.currentGardenSquares }),
        queryClient.invalidateQueries({ queryKey: queryKeys.plantings.specificPlantsAll }),
        queryClient.invalidateQueries({ queryKey: queryKeys.plantings.plantLifecycleAll }),
        queryClient.invalidateQueries({ queryKey: queryKeys.plantings.batchesAll })
      ])
  })

  function updateGardenAreaFilter(event: React.ChangeEvent<HTMLSelectElement>) {
    const { value } = event.target
    if (value === 'all') {
      setFilterGardenArea(undefined)
      return
    }
    setFilterGardenArea(Number(value))
  }

  function updateGardenBedFilter(event: React.ChangeEvent<HTMLSelectElement>) {
    const { value } = event.target
    if (value === 'all') {
      setFilterGardenBed(undefined)
      return
    }
    setFilterGardenBed(value)
  }

  async function createPlanting(data: GardenSquareDirectPlantingCreate) {
    await createMutation.mutateAsync(data)
  }

  async function completePlanting(planting: GardenSquarePlanting) {
    if (planting.specific_plant_pk) {
      // An individual plant records the outcome itself; the service closes its
      // location in the same transaction.
      await finishHarvestMutation.mutateAsync(planting.specific_plant_pk)
    } else if (planting.transplanted && planting.transplanting_pk) {
      await completeTransplantMutation.mutateAsync(planting.transplanting_pk)
    } else {
      await completeDirectMutation.mutateAsync(planting.planting_pk)
    }
  }

  async function correctPlanting(plantingPk: number, data: SowingCorrection) {
    await correctionMutation.mutateAsync({ pk: plantingPk, data })
  }

  const areas = gardenAreas.map((area) => (
    <option value={area.pk} key={area.pk}>
      {area.name}
    </option>
  ))
  const beds = gardenBeds
    .filter((bed) => filterGardenArea && bed.area === filterGardenArea)
    .map((bed) => (
      <option value={bed.name} key={bed.name}>
        {bed.name}
      </option>
    ))
  areas.unshift(
    <option key="all" value="all">
      All Areas
    </option>
  )
  beds.unshift(
    <option key="all" value="all">
      All Beds
    </option>
  )
  const rows = []
  if (showPlantingAdd) {
    rows.push(
      <NewGardenSquarePlantingRow
        key="new"
        seedPackets={seedPackets}
        seeds={seeds}
        packetOptions={packetSelectOptions(seedPackets, seeds, suppliers, varieties)}
        gardenSquares={gardenSquares}
        gardenBeds={gardenBeds}
        createPlanting={createPlanting}
        done={() => setShowPlantingAdd(false)}
      />
    )
  }
  for (const planting of plantings) {
    if (
      !filterGardenArea ||
      (gardenAreas.find((area) => area.pk === filterGardenArea)?.name === planting.location.area && (!filterGardenBed || filterGardenBed === planting.location.bed))
    ) {
      rows.push(
        <GardenSquarePlantingRow
          key={planting.transplanting_pk ? 't' + planting.transplanting_pk : planting.planting_pk}
          planting={planting}
          seedPackets={seedPackets}
          completePlanting={completePlanting}
          correctPlanting={correctPlanting}
        />
      )
    }
  }
  return (
    <Table>
      <thead>
        <tr key="header">
          <td>
            Plant{' '}
            <Button variant="link" className="p-0 align-baseline" aria-label="Add garden square planting" onClick={() => setShowPlantingAdd(true)}>
              +
            </Button>
          </td>
          <td>Batch</td>
          <td>Quantity</td>
          <td>Date</td>
          <td>Location</td>
          <td>Expected Germination</td>
          <td>Expected Maturity</td>
          <td>Notes</td>
        </tr>
        <tr key="filters">
          <td></td>
          <td></td>
          <td></td>
          <td></td>
          <td>
            <select onChange={updateGardenAreaFilter}>{areas}</select>
            <select onChange={updateGardenBedFilter}>{beds}</select>
          </td>
          <td></td>
          <td></td>
          <td></td>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </Table>
  )
}

export { SeedTrayPlantingTable, GardenSquarePlantingTable, SeedTrayPlantingRow }
