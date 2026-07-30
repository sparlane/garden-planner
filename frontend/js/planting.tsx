import 'bootstrap'
import 'bootstrap/dist/css/bootstrap.css'

import React from 'react'
import { Table, Button } from 'react-bootstrap'
import Select from 'react-select'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { Supplier } from './types/suppliers'
import { PlantVariety } from './types/plants'
import { Seed, SeedPacket } from './types/seeds'
import { GardenBed, GardenSquare } from './types/garden'
import { GardenSquareDirectPlantingCreate, GardenSquarePlanting, SeedTrayPlantingCreate, SeedTrayPlantingDetails } from './types/plantings'
import { SeedTray, SeedTrayModel } from './types/seedtrays'
import { SelectOption } from './types/others'
import { getGardenAreas, getGardenBeds, getGardenSquares } from './api/garden'
import { formatDate, formatDateRange } from './utils'
import {
  getPlantingSeedTrayCurrent,
  getPlantingGardenSquaresCurrent,
  addPlantingDirectSowGardenSquare,
  addPlantingSeedTray,
  completePlantingDirectSowGardenSquare,
  completePlantingTransplantedGardenSquare,
  completePlantingSeedTray,
  endSpecificPlantLocation
} from './api/plantings'
import { getPlantVarieties } from './api/plants'
import { getSeedPackets, getSeeds } from './api/seeds'
import { getSeedTrayModels, getSeedTrays, getSeedTrayCells } from './api/seedtrays'
import { SeedTrayCell } from './types/seedtrays'
import { getSuppliers } from './api/supplies'
import { queryKeys } from './query'

interface SeedTrayCellGridProps {
  cells: Array<SeedTrayCell>
  cellQuantities: { [cellPk: number]: number }
  quantity: number
  onUpdateCellQuantity: (cellPk: number, qty: number) => void
}

class SeedTrayCellGrid extends React.PureComponent<SeedTrayCellGridProps> {
  render() {
    const { cells, cellQuantities, quantity, onUpdateCellQuantity } = this.props
    const cellsPerRow = 8
    const cellGridRows = []

    for (let i = 0; i < cells.length; i += cellsPerRow) {
      const rowCells = cells.slice(i, i + cellsPerRow)
      cellGridRows.push(
        <tr key={i}>
          {rowCells.map((cell) => (
            <td key={cell.pk} style={{ padding: '8px', border: '1px solid #ccc', textAlign: 'center' }}>
              <div style={{ fontWeight: 'bold', marginBottom: '4px' }}>
                Cell {cell.x_position}, {cell.y_position}
              </div>
              <input
                type="number"
                min="0"
                placeholder="Qty"
                value={cellQuantities[cell.pk] || ''}
                onChange={(e) => {
                  const rawValue = e.target.value
                  const parsed = parseInt(rawValue, 10)

                  if (Number.isNaN(parsed)) {
                    return
                  }

                  const clamped = Math.max(0, parsed)
                  onUpdateCellQuantity(cell.pk, clamped)
                }}
                style={{ width: '50px' }}
              />
            </td>
          ))}
        </tr>
      )
    }

    const cellTotal = Object.values(cellQuantities).reduce((sum, qty) => sum + qty, 0)

    return (
      <div style={{ marginBottom: '8px' }}>
        <div style={{ fontWeight: 'bold', marginBottom: '8px' }}>
          Select cells and quantities ({cellTotal} / {quantity}):
        </div>
        <Table border={1} cellPadding={5} cellSpacing={0} style={{ width: 'auto' }}>
          <tbody>{cellGridRows}</tbody>
        </Table>
      </div>
    )
  }
}

interface NewSeedTrayPlantingRowProps {
  suppliers: Array<Supplier>
  varieties: Array<PlantVariety>
  seeds: Array<Seed>
  seedPackets: Array<SeedPacket>
  seedTrays: Array<SeedTray>
  seedTrayModels: { [key: number]: SeedTrayModel }
  done: () => void
  createPlanting: (data: SeedTrayPlantingCreate) => Promise<void>
}

function NewSeedTrayPlantingRow({ suppliers, varieties, seeds, seedPackets, seedTrays, seedTrayModels, done, createPlanting }: NewSeedTrayPlantingRowProps) {
  const [seedPacket, setSeedPacket] = React.useState<number>()
  const [quantity, setQuantity] = React.useState(1)
  const [seedTray, setSeedTray] = React.useState<number>()
  const [location, setLocation] = React.useState<string>()
  const [notes, setNotes] = React.useState<string>()
  const [cellQuantities, setCellQuantities] = React.useState<Record<number, number>>({})
  const [error, setError] = React.useState<string>()
  const { data: seedTrayCells = [] } = useQuery({
    queryKey: queryKeys.seedTrays.cells(seedTray ?? 0),
    queryFn: ({ signal }) => getSeedTrayCells(seedTray as number, signal),
    enabled: Boolean(seedTray)
  })

  function updateSeedPacket(event: React.ChangeEvent<HTMLSelectElement>) {
    const { value } = event.target

    if (value === '' || value === undefined || value === null) {
      setSeedPacket(undefined)
      return
    }
    setSeedPacket(Number(value))
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
    const value = selectedSeedTray?.value
    setCellQuantities({})
    setSeedTray(value === undefined || value === null ? undefined : Number(value))
  }

  function updateCellQuantity(cellPk: number, qty: number) {
    setCellQuantities((currentQuantities) => {
      const updated = { ...currentQuantities }
      if (qty > 0) {
        updated[cellPk] = qty
      } else {
        delete updated[cellPk]
      }
      return updated
    })
  }

  async function add() {
    if (seedPacket === undefined) {
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

  const packetOptions = [<option key="blank"></option>]
  for (const seedPacketData of seedPackets) {
    const seedData = seeds.find((candidate) => candidate.pk === seedPacketData.seeds)
    const supplier = suppliers.find((candidate) => candidate.pk === seedData?.supplier)
    const variety = varieties.find((candidate) => candidate.pk === seedData?.plant_variety)
    packetOptions.push(
      <option key={seedPacketData.pk} value={seedPacketData.pk}>
        {variety?.name} from {supplier?.name} (Sow By: {seedPacketData.sow_by})
      </option>
    )
  }
  const trayOptions = seedTrays.map((tray) => ({ value: tray.pk, label: `${tray.pk} (${seedTrayModels[tray.model]?.description})` }))

  return (
    <>
      {error && (
        <tr>
          <td colSpan={7} style={{ padding: '8px', backgroundColor: '#f8d7da', color: '#721c24', border: '1px solid #f5c6cb' }}>
            <strong>Error:</strong> {error}
          </td>
        </tr>
      )}
      <tr>
        <td>
          <select onChange={updateSeedPacket}>{packetOptions}</select>
        </td>
        <td>
          <input type="number" defaultValue={quantity} onChange={updateQuantity} />
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
      {seedTray !== undefined && seedTrayCells.length > 0 && (
        <tr>
          <td colSpan={7} style={{ padding: '16px' }}>
            <div style={{ marginBottom: '8px', fontWeight: 'bold' }}>Select cells and quantities:</div>
            <SeedTrayCellGrid cells={seedTrayCells} cellQuantities={cellQuantities} quantity={quantity} onUpdateCellQuantity={updateCellQuantity} />
          </td>
        </tr>
      )}
    </>
  )
}

interface SeedTrayPlantingRowProps {
  planting: SeedTrayPlantingDetails
  completePlanting: (plantingPk: number) => Promise<void>
}

class SeedTrayPlantingRow extends React.Component<SeedTrayPlantingRowProps> {
  constructor(props: SeedTrayPlantingRowProps) {
    super(props)

    this.empty = this.empty.bind(this)
  }

  async empty() {
    await this.props.completePlanting(this.props.planting.pk)
  }

  render() {
    return (
      <tr>
        <td>
          {this.props.planting.plant} - {this.props.planting.variety}
        </td>
        <td>
          {this.props.planting.quantity} (<span title="Number that have germinated">Germinated: {this.props.planting.germinated_count}</span>,{' '}
          <span title="Number that have been transplanted to a garden square">Transplanted: {this.props.planting.transplanted_count}</span>)
        </td>
        <td>{formatDate(this.props.planting.planted)}</td>
        <td>{this.props.planting.seed_tray}</td>
        <td>{this.props.planting.location}</td>
        <td>{formatDateRange(this.props.planting.germination_date_early, this.props.planting.germination_date_late)}</td>
        <td>{this.props.planting.notes}</td>
        <td>
          {this.props.planting.seed_tray && (
            <a className="btn btn-primary" href={`/seedtrays/seedtray/${this.props.planting.seed_tray}/`}>
              Manage Plants
            </a>
          )}
          <Button onClick={this.empty}>Empty</Button>
        </td>
      </tr>
    )
  }
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

  const rows = []
  if (showPlantingAdd) {
    rows.push(
      <NewSeedTrayPlantingRow
        key="new"
        seedPackets={seedPackets}
        seeds={seeds}
        suppliers={suppliers}
        varieties={varieties}
        seedTrays={seedTrays}
        seedTrayModels={seedTrayModels}
        createPlanting={createPlanting}
        done={() => setShowPlantingAdd(false)}
      />
    )
  }
  for (const planting of plantings) {
    rows.push(<SeedTrayPlantingRow key={planting.pk} planting={planting} completePlanting={completePlanting} />)
  }
  return (
    <Table>
      <thead>
        <tr>
          <td>
            Plant{' '}
            <a href="#" onClick={() => setShowPlantingAdd(true)}>
              +
            </a>
          </td>
          <td>Quantity</td>
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
  suppliers: Array<Supplier>
  varieties: Array<PlantVariety>
  seeds: Array<Seed>
  seedPackets: Array<SeedPacket>
  gardenBeds: Array<GardenBed>
  gardenSquares: Array<GardenSquare>
  done: () => void
  createPlanting: (data: GardenSquareDirectPlantingCreate) => Promise<void>
}

interface NewGardenSquarePlantingRowState {
  seedPacket?: number
  quantity: number
  location?: number
  notes?: string
}

class NewGardenSquarePlantingRow extends React.Component<NewGardenSquarePlantingRowProps, NewGardenSquarePlantingRowState> {
  constructor(props: NewGardenSquarePlantingRowProps) {
    super(props)

    this.state = {
      seedPacket: undefined,
      quantity: 1,
      location: undefined,
      notes: undefined
    }

    this.updateSeedPacket = this.updateSeedPacket.bind(this)
    this.updateQuantity = this.updateQuantity.bind(this)
    this.updateLocation = this.updateLocation.bind(this)
    this.updateNotes = this.updateNotes.bind(this)

    this.add = this.add.bind(this)
  }

  updateSeedPacket(selectedSeedPacket: SelectOption | null) {
    const value = selectedSeedPacket?.value
    if (value === undefined || value === null) {
      this.setState({ seedPacket: undefined })
      return
    }
    this.setState({ seedPacket: Number(value) })
  }

  updateQuantity(event: React.ChangeEvent<HTMLInputElement>) {
    const { value } = event.target

    if (value === '' || value === undefined || value === null) {
      this.setState({ quantity: 0 })
      return
    }
    this.setState({ quantity: Number(value) })
  }

  updateLocation(selectedLocation: SelectOption | null) {
    const value = selectedLocation?.value
    if (value === undefined || value === null) {
      this.setState({ location: undefined })
      return
    }
    this.setState({ location: Number(value) })
  }

  updateNotes(event: React.ChangeEvent<HTMLTextAreaElement>) {
    const { value } = event.target

    this.setState({ notes: value })
  }

  async add() {
    if (this.state.seedPacket === undefined || this.state.location === undefined) {
      return
    }
    const data: GardenSquareDirectPlantingCreate = {
      seeds_used: this.state.seedPacket,
      quantity: this.state.quantity,
      location: this.state.location,
      notes: this.state.notes
    }
    await this.props.createPlanting(data)
    this.props.done()
  }

  render() {
    const seedPackets = []
    for (const sp in this.props.seedPackets) {
      const seedPacketData = this.props.seedPackets[sp]
      const seeds = this.props.seeds.find((s) => s.pk === seedPacketData.seeds)
      const supplier = this.props.suppliers.find((s) => s.pk === seeds?.supplier)
      const variety = this.props.varieties.find((v) => v.pk === seeds?.plant_variety)
      seedPackets.push({ value: seedPacketData.pk, label: `${variety?.name} from ${supplier?.name} (Sow By: ${seedPacketData.sow_by})` })
    }
    const locations = []
    for (const b in this.props.gardenBeds) {
      const gardenBedData = this.props.gardenBeds[b]
      const bedSquares = this.props.gardenSquares.filter((s) => s.bed === gardenBedData.pk)
      for (const l in bedSquares) {
        const gardenSquareData = bedSquares[l]
        locations.push({ value: gardenSquareData.pk, label: `${gardenBedData.name} - ${gardenSquareData.name}` })
      }
    }
    return (
      <tr>
        <td>
          <Select onChange={this.updateSeedPacket} options={seedPackets} value={seedPackets.find((o) => o.value === this.state.seedPacket)} />
        </td>
        <td>
          <input type="number" defaultValue={this.state.quantity} onChange={this.updateQuantity} />
        </td>
        <td></td>
        <td>
          <Select onChange={this.updateLocation} options={locations} value={locations.find((o) => o.value === this.state.location)} />
        </td>
        <td>
          <textarea onChange={this.updateNotes} />
        </td>
        <td>
          <Button onClick={this.add}>Add</Button>
          <Button onClick={this.props.done}>Cancel</Button>
        </td>
      </tr>
    )
  }
}

interface GardenSquarePlantingRowProps {
  planting: GardenSquarePlanting
  completePlanting: (planting: GardenSquarePlanting) => Promise<void>
}

class GardenSquarePlantingRow extends React.Component<GardenSquarePlantingRowProps> {
  constructor(props: GardenSquarePlantingRowProps) {
    super(props)

    this.empty = this.empty.bind(this)
  }

  async empty() {
    await this.props.completePlanting(this.props.planting)
  }

  render() {
    let planted = ''
    if (this.props.planting.transplanted) {
      planted = `${formatDate(this.props.planting.transplanted)} (S: ${formatDate(this.props.planting.planted)})`
    } else {
      planted = formatDate(this.props.planting.planted)
    }
    return (
      <tr>
        <td>
          {this.props.planting.plant} - {this.props.planting.variety}
        </td>
        <td>{this.props.planting.quantity}</td>
        <td>{planted}</td>
        <td>
          {this.props.planting.location.area} - {this.props.planting.location.bed} - {this.props.planting.location.name}
        </td>
        <td>{formatDateRange(this.props.planting.germination_date_early, this.props.planting.germination_date_late)}</td>
        <td>{formatDateRange(this.props.planting.maturity_date_early, this.props.planting.maturity_date_late)}</td>
        <td>{this.props.planting.notes}</td>
        <td>
          <Button onClick={this.empty}>Harvested</Button>
        </td>
      </tr>
    )
  }
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
  const endLocationMutation = useMutation({
    mutationFn: endSpecificPlantLocation,
    onSuccess: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.plantings.currentGardenSquares }),
        queryClient.invalidateQueries({ queryKey: queryKeys.plantings.specificPlantsAll })
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
    if (planting.specific_plant_pk && planting.transplanting_pk) {
      await endLocationMutation.mutateAsync(planting.transplanting_pk)
    } else if (planting.transplanted && planting.transplanting_pk) {
      await completeTransplantMutation.mutateAsync(planting.transplanting_pk)
    } else {
      await completeDirectMutation.mutateAsync(planting.planting_pk)
    }
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
        suppliers={suppliers}
        varieties={varieties}
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
        <GardenSquarePlantingRow key={planting.transplanting_pk ? 't' + planting.transplanting_pk : planting.planting_pk} planting={planting} completePlanting={completePlanting} />
      )
    }
  }
  return (
    <Table>
      <thead>
        <tr key="header">
          <td>
            Plant{' '}
            <a href="#" onClick={() => setShowPlantingAdd(true)}>
              +
            </a>
          </td>
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
