import 'bootstrap'
import 'bootstrap/dist/css/bootstrap.css'

import React from 'react'
import { Table, Button } from 'react-bootstrap'
import Select from 'react-select'

import $ from 'jquery'

import { Supplier } from './types/suppliers'
import { PlantVariety } from './types/plants'
import { Seed, SeedPacket } from './types/seeds'
import { GardenArea, GardenBed, GardenSquare } from './types/garden'
import { GardenSquarePlanting, SeedTrayPlantingDetails } from './types/plantings'
import { SeedTray, SeedTrayModel } from './types/seedtrays'
import { SelectOption } from './types/others'
import { csrfPost } from './utils'

interface NewSeedTrayPlantingRowProps {
  suppliers: Array<Supplier>
  varieties: Array<PlantVariety>
  seeds: Array<Seed>
  seedPackets: Array<SeedPacket>
  seedTrays: Array<SeedTray>
  seedTrayModels: { [key: number]: SeedTrayModel }
  done: () => void
}

interface NewSeedTrayPlantingRowState {
  seedPacket?: number
  quantity: number
  seedTray?: number
  location?: string
  notes?: string
}

class NewSeedTrayPlantingRow extends React.Component<NewSeedTrayPlantingRowProps, NewSeedTrayPlantingRowState> {
  constructor(props: NewSeedTrayPlantingRowProps) {
    super(props)

    this.state = {
      seedPacket: undefined,
      quantity: 1,
      seedTray: undefined,
      location: undefined,
      notes: undefined
    }

    this.updateSeedPacket = this.updateSeedPacket.bind(this)
    this.updateQuantity = this.updateQuantity.bind(this)
    this.updateLocation = this.updateLocation.bind(this)
    this.updateNotes = this.updateNotes.bind(this)
    this.updateSeedTray = this.updateSeedTray.bind(this)

    this.add = this.add.bind(this)
  }

  updateSeedPacket(event: React.ChangeEvent<HTMLSelectElement>) {
    const { value } = event.target

    if (value === '' || value === undefined || value === null) {
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

  updateLocation(event: React.ChangeEvent<HTMLInputElement>) {
    const { value } = event.target

    this.setState({ location: value })
  }

  updateNotes(event: React.ChangeEvent<HTMLTextAreaElement>) {
    const { value } = event.target

    this.setState({ notes: value })
  }

  updateSeedTray(selectedSeedTray: SelectOption | null) {
    const value = selectedSeedTray?.value
    if (value === undefined || value === null) {
      this.setState({ seedTray: undefined })
      return
    }
    this.setState({ seedTray: Number(value) })
  }

  add() {
    const data = {
      seeds_used: this.state.seedPacket,
      quantity: this.state.quantity,
      location: this.state.location,
      seed_tray: this.state.seedTray,
      notes: this.state.notes
    }
    csrfPost('/plantings/seedtray/', data).done(this.props.done)
  }

  render() {
    const seedPackets = [<option key="blank"></option>]
    for (const sp in this.props.seedPackets) {
      const seedPacketData = this.props.seedPackets[sp]
      const seeds = this.props.seeds.find((s) => s.pk === seedPacketData.seeds)
      const supplier = this.props.suppliers.find((s) => s.pk === seeds?.supplier)
      const variety = this.props.varieties.find((v) => v.pk === seeds?.plant_variety)
      seedPackets.push(
        <option key={seedPacketData.pk} value={seedPacketData.pk}>
          {variety?.name} from {supplier?.name} (Sow By: {seedPacketData.sow_by})
        </option>
      )
    }
    const seedTrays = []
    for (const seedTrayData of this.props.seedTrays) {
      seedTrays.push({ value: seedTrayData.pk, label: `${seedTrayData.pk} (${this.props.seedTrayModels[seedTrayData.model]?.description})` })
    }
    return (
      <tr>
        <td>
          <select onChange={this.updateSeedPacket}>{seedPackets}</select>
        </td>
        <td>
          <input type="number" defaultValue={this.state.quantity} onChange={this.updateQuantity} />
        </td>
        <td></td>
        <td>
          <Select onChange={this.updateSeedTray} options={seedTrays} value={seedTrays.find((o) => o.value === this.state.seedTray)} />
        </td>
        <td>
          <input type="text" onChange={this.updateLocation} />
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

interface SeedTrayTransplantingGardenSquareRowProps {
  planting: SeedTrayPlantingDetails
  gardenBeds: Array<GardenBed>
  gardenSquares: Array<GardenSquare>
  done: () => void
}

interface SeedTrayTransplantingGardenSquareRowState {
  quantity: number
  location?: number
  notes?: string
}

class SeedTrayTransplantingGardenSquareRow extends React.Component<SeedTrayTransplantingGardenSquareRowProps, SeedTrayTransplantingGardenSquareRowState> {
  constructor(props: SeedTrayTransplantingGardenSquareRowProps) {
    super(props)

    this.state = {
      quantity: 1,
      location: undefined,
      notes: undefined
    }

    this.updateQuantity = this.updateQuantity.bind(this)
    this.updateLocation = this.updateLocation.bind(this)
    this.updateNotes = this.updateNotes.bind(this)

    this.add = this.add.bind(this)
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
    } else {
      this.setState({ location: Number(value) })
    }
  }

  updateNotes(event: React.ChangeEvent<HTMLTextAreaElement>) {
    const { value } = event.target

    this.setState({ notes: value })
  }

  add() {
    const data = {
      original_planting: this.props.planting.pk,
      quantity: this.state.quantity,
      location: this.state.location,
      notes: this.state.notes
    }
    csrfPost('/plantings/transplantedgardensquare/', data).done(this.props.done)
  }

  render() {
    const locations: Array<SelectOption> = []
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
          {this.props.planting.plant} - {this.props.planting.variety}
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
          <Button onClick={this.add}>Transplant</Button>
          <Button onClick={this.props.done}>Cancel</Button>
        </td>
      </tr>
    )
  }
}

interface SeedTrayPlantingRowProps {
  planting: SeedTrayPlantingDetails
  transplantAction: (planting: SeedTrayPlantingDetails) => void
}

class SeedTrayPlantingRow extends React.Component<SeedTrayPlantingRowProps> {
  constructor(props: SeedTrayPlantingRowProps) {
    super(props)

    this.transplant = this.transplant.bind(this)
    this.empty = this.empty.bind(this)
  }

  transplant() {
    this.props.transplantAction(this.props.planting)
  }

  empty() {
    csrfPost('/plantings/seedtray/complete/', { planting: this.props.planting.pk })
  }

  render() {
    return (
      <tr>
        <td>
          {this.props.planting.plant} - {this.props.planting.variety}
        </td>
        <td>
          {this.props.planting.quantity} (<span title="Number that have been transplanted to a garden square">Transplanted: {this.props.planting.transplanted_count}</span>)
        </td>
        <td>{this.props.planting.planted}</td>
        <td>{this.props.planting.seed_tray}</td>
        <td>{this.props.planting.location}</td>
        <td>
          {this.props.planting.germination_date_early} - {this.props.planting.germination_date_late}
        </td>
        <td>{this.props.planting.notes}</td>
        <td>
          <Button onClick={this.transplant}>Transplant</Button>
          <Button onClick={this.empty}>Empty</Button>
        </td>
      </tr>
    )
  }
}

interface SeedTrayPlantingTableState {
  showPlantingAdd: boolean
  showTransplanting?: SeedTrayPlantingDetails
  suppliers: Array<Supplier>
  varieties: Array<PlantVariety>
  seeds: Array<Seed>
  seedPackets: Array<SeedPacket>
  seedTrays: Array<SeedTray>
  seedTrayModels: { [key: number]: SeedTrayModel }
  plantings: Array<SeedTrayPlantingDetails>
  gardenSquares: Array<GardenSquare>
  gardenBeds: Array<GardenBed>
}

class SeedTrayPlantingTable extends React.Component<undefined, SeedTrayPlantingTableState> {
  timer?: number

  constructor(props: undefined) {
    super(props)

    this.state = {
      showPlantingAdd: false,
      showTransplanting: undefined,
      suppliers: [],
      varieties: [],
      seeds: [],
      seedPackets: [],
      seedTrays: [],
      seedTrayModels: {},
      plantings: [],
      gardenSquares: [],
      gardenBeds: []
    }

    this.showNewPlantingAdd = this.showNewPlantingAdd.bind(this)
    this.hideNewPlantingAdd = this.hideNewPlantingAdd.bind(this)

    this.showTransplanting = this.showTransplanting.bind(this)
    this.hideTransplanting = this.hideTransplanting.bind(this)

    this.updateData = this.updateData.bind(this)
    this.updateSupplierList = this.updateSupplierList.bind(this)
    this.updateVarietiesList = this.updateVarietiesList.bind(this)
    this.updateSeedList = this.updateSeedList.bind(this)
    this.updateSeedPacketList = this.updateSeedPacketList.bind(this)
    this.updatePlantingList = this.updatePlantingList.bind(this)
    this.updateGardenSquares = this.updateGardenSquares.bind(this)
    this.updateGardenBeds = this.updateGardenBeds.bind(this)
    this.updateSeedTrays = this.updateSeedTrays.bind(this)
    this.updateSeedTrayModels = this.updateSeedTrayModels.bind(this)
  }

  showNewPlantingAdd() {
    this.setState({
      showPlantingAdd: true
    })
  }

  hideNewPlantingAdd() {
    this.setState({
      showPlantingAdd: false
    })
  }

  showTransplanting(planting: SeedTrayPlantingDetails) {
    this.setState({
      showTransplanting: planting
    })
  }

  hideTransplanting() {
    this.setState({
      showTransplanting: undefined
    })
  }

  componentDidMount() {
    this.updateData()
    this.timer = setInterval(() => this.updateData(), 10000)
  }

  componentWillUnmount() {
    clearInterval(this.timer)
    this.timer = undefined
  }

  updateSupplierList(data: Array<Supplier>) {
    this.setState({
      suppliers: data
    })
  }

  updateVarietiesList(data: Array<PlantVariety>) {
    this.setState({
      varieties: data
    })
  }

  updateSeedList(data: Array<Seed>) {
    this.setState({
      seeds: data
    })
  }

  updateSeedPacketList(data: Array<SeedPacket>) {
    this.setState({
      seedPackets: data
    })
  }

  updatePlantingList(data: { plantings: Array<SeedTrayPlantingDetails> }) {
    this.setState({
      plantings: data.plantings
    })
  }

  updateGardenSquares(data: Array<GardenSquare>) {
    this.setState({
      gardenSquares: data
    })
  }

  updateGardenBeds(data: Array<GardenBed>) {
    this.setState({
      gardenBeds: data
    })
  }

  updateSeedTrays(data: Array<SeedTray>) {
    this.setState({
      seedTrays: data
    })
  }

  updateSeedTrayModels(data: Array<SeedTrayModel>) {
    this.setState({
      seedTrayModels: data.reduce((acc: { [key: number]: SeedTrayModel }, model: SeedTrayModel) => {
        acc[model.pk] = model
        return acc
      }, {})
    })
  }

  async updateData() {
    await $.getJSON('/supplies/supplier/', this.updateSupplierList)
    await $.getJSON('/plants/variety/', this.updateVarietiesList)
    await $.getJSON('/seeds/seeds/', this.updateSeedList)
    await $.getJSON('/seeds/packets/', this.updateSeedPacketList)
    await $.getJSON('/plantings/seedtray/current/', this.updatePlantingList)
    await $.getJSON('/garden/squares/', this.updateGardenSquares)
    await $.getJSON('/garden/beds/', this.updateGardenBeds)
    await $.getJSON('/seedtrays/seedtrays/', this.updateSeedTrays)
    await $.getJSON('/seedtrays/seedtraymodels/', this.updateSeedTrayModels)
  }

  render() {
    const rows = []
    if (this.state.showPlantingAdd) {
      rows.push(
        <NewSeedTrayPlantingRow
          key="new"
          seedPackets={this.state.seedPackets}
          seeds={this.state.seeds}
          suppliers={this.state.suppliers}
          varieties={this.state.varieties}
          seedTrays={this.state.seedTrays}
          seedTrayModels={this.state.seedTrayModels}
          done={this.hideNewPlantingAdd}
        />
      )
    }
    for (const p in this.state.plantings) {
      const plantingData = this.state.plantings[p]
      rows.push(<SeedTrayPlantingRow key={plantingData.pk} planting={plantingData} transplantAction={this.showTransplanting} />)
      if (this.state.showTransplanting !== undefined && this.state.showTransplanting?.pk === this.state.plantings[p].pk) {
        rows.push(
          <SeedTrayTransplantingGardenSquareRow
            key="transplanting"
            planting={plantingData}
            done={this.hideTransplanting}
            gardenBeds={this.state.gardenBeds}
            gardenSquares={this.state.gardenSquares}
          />
        )
      }
    }
    return (
      <Table>
        <thead>
          <tr>
            <td>
              Plant{' '}
              <a href="#" onClick={this.showNewPlantingAdd}>
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
}

interface NewGardenSquarePlantingRowProps {
  suppliers: Array<Supplier>
  varieties: Array<PlantVariety>
  seeds: Array<Seed>
  seedPackets: Array<SeedPacket>
  gardenBeds: Array<GardenBed>
  gardenSquares: Array<GardenSquare>
  done: () => void
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

  add() {
    const data = {
      seeds_used: this.state.seedPacket,
      quantity: this.state.quantity,
      location: this.state.location,
      notes: this.state.notes
    }
    csrfPost('/plantings/directsowgardensquare/', data).done(this.props.done)
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
}

class GardenSquarePlantingRow extends React.Component<GardenSquarePlantingRowProps> {
  constructor(props: GardenSquarePlantingRowProps) {
    super(props)

    this.empty = this.empty.bind(this)
  }

  empty() {
    csrfPost(this.props.planting.transplanted ? '/plantings/garden/squares/transplant/complete/' : '/plantings/garden/squares/complete/', {
      planting: this.props.planting.transplanted ? this.props.planting.transplanting_pk : this.props.planting.planting_pk
    })
  }

  render() {
    let planted = ''
    if (this.props.planting.transplanted) {
      planted = `${this.props.planting.transplanted} (S: ${this.props.planting.planted})`
    } else {
      planted = this.props.planting.planted
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
        <td>
          {this.props.planting.germination_date_early} - {this.props.planting.germination_date_late}
        </td>
        <td>
          {this.props.planting.maturity_date_early} - {this.props.planting.maturity_date_late}
        </td>
        <td>{this.props.planting.notes}</td>
        <td>
          <Button onClick={this.empty}>Harvested</Button>
        </td>
      </tr>
    )
  }
}

interface GardenSquarePlantingTableState {
  showPlantingAdd: boolean
  suppliers: Array<Supplier>
  varieties: Array<PlantVariety>
  seeds: Array<Seed>
  seedPackets: Array<SeedPacket>
  plantings: Array<GardenSquarePlanting>
  gardenSquares: Array<GardenSquare>
  gardenBeds: Array<GardenBed>
  gardenAreas: Array<GardenArea>
  filterGardenArea?: number
  filterGardenBed?: string
}

class GardenSquarePlantingTable extends React.Component<undefined, GardenSquarePlantingTableState> {
  timer?: number

  constructor(props: undefined) {
    super(props)

    this.state = {
      showPlantingAdd: false,
      suppliers: [],
      varieties: [],
      seeds: [],
      seedPackets: [],
      plantings: [],
      gardenSquares: [],
      gardenBeds: [],
      gardenAreas: [],
      filterGardenArea: undefined,
      filterGardenBed: undefined
    }

    this.showNewPlantingAdd = this.showNewPlantingAdd.bind(this)
    this.hideNewPlantingAdd = this.hideNewPlantingAdd.bind(this)

    this.updateData = this.updateData.bind(this)
    this.updateSupplierList = this.updateSupplierList.bind(this)
    this.updateVarietiesList = this.updateVarietiesList.bind(this)
    this.updateSeedList = this.updateSeedList.bind(this)
    this.updateSeedPacketList = this.updateSeedPacketList.bind(this)
    this.updatePlantingList = this.updatePlantingList.bind(this)
    this.updateGardenSquares = this.updateGardenSquares.bind(this)
    this.updateGardenBeds = this.updateGardenBeds.bind(this)
    this.updateGardenAreas = this.updateGardenAreas.bind(this)
    this.updateGardenAreaFilter = this.updateGardenAreaFilter.bind(this)
    this.updateGardenBedFilter = this.updateGardenBedFilter.bind(this)
  }

  showNewPlantingAdd() {
    this.setState({
      showPlantingAdd: true
    })
  }

  hideNewPlantingAdd() {
    this.setState({
      showPlantingAdd: false
    })
  }

  componentDidMount() {
    this.updateData()
    this.timer = setInterval(() => this.updateData(), 10000)
  }

  componentWillUnmount() {
    clearInterval(this.timer)
    this.timer = undefined
  }

  updateSupplierList(data: Array<Supplier>) {
    this.setState({
      suppliers: data
    })
  }

  updateVarietiesList(data: Array<PlantVariety>) {
    this.setState({
      varieties: data
    })
  }

  updateSeedList(data: Array<Seed>) {
    this.setState({
      seeds: data
    })
  }

  updateSeedPacketList(data: Array<SeedPacket>) {
    this.setState({
      seedPackets: data
    })
  }

  updatePlantingList(data: { plantings: Array<GardenSquarePlanting> }) {
    this.setState({
      plantings: data.plantings
    })
  }

  updateGardenSquares(data: Array<GardenSquare>) {
    this.setState({
      gardenSquares: data
    })
  }

  updateGardenBeds(data: Array<GardenBed>) {
    this.setState({
      gardenBeds: data
    })
  }

  updateGardenAreas(data: Array<GardenArea>) {
    this.setState({
      gardenAreas: data
    })
  }

  updateGardenAreaFilter(event: React.ChangeEvent<HTMLSelectElement>) {
    const { value } = event.target
    if (value === 'all') {
      this.setState({ filterGardenArea: undefined })
      return
    }
    this.setState({ filterGardenArea: Number(value) })
  }

  updateGardenBedFilter(event: React.ChangeEvent<HTMLSelectElement>) {
    const { value } = event.target
    if (value === 'all') {
      this.setState({ filterGardenBed: undefined })
      return
    }
    this.setState({ filterGardenBed: value })
  }

  async updateData() {
    await $.getJSON('/supplies/supplier/', this.updateSupplierList)
    await $.getJSON('/plants/variety/', this.updateVarietiesList)
    await $.getJSON('/seeds/seeds/', this.updateSeedList)
    await $.getJSON('/seeds/packets/', this.updateSeedPacketList)
    await $.getJSON('/plantings/garden/squares/current/', this.updatePlantingList)
    await $.getJSON('/garden/areas/', this.updateGardenAreas)
    await $.getJSON('/garden/squares/', this.updateGardenSquares)
    await $.getJSON('/garden/beds/', this.updateGardenBeds)
  }

  render() {
    const areas = this.state.gardenAreas.map((area) => (
      <option value={area.pk} key={area.pk}>
        {area.name}
      </option>
    ))
    const beds = this.state.gardenBeds
      .filter((bed) => this.state.filterGardenArea && bed.area === this.state.filterGardenArea)
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
    if (this.state.showPlantingAdd) {
      rows.push(
        <NewGardenSquarePlantingRow
          key="new"
          seedPackets={this.state.seedPackets}
          seeds={this.state.seeds}
          suppliers={this.state.suppliers}
          varieties={this.state.varieties}
          gardenSquares={this.state.gardenSquares}
          gardenBeds={this.state.gardenBeds}
          done={this.hideNewPlantingAdd}
        />
      )
    }
    for (const p in this.state.plantings) {
      const plantingData = this.state.plantings[p]
      if (
        !this.state.filterGardenArea ||
        (this.state.gardenAreas.find((area) => area.pk === this.state.filterGardenArea)?.name === plantingData.location.area &&
          (!this.state.filterGardenBed || this.state.filterGardenBed === plantingData.location.bed))
      ) {
        rows.push(<GardenSquarePlantingRow key={plantingData.transplanting_pk ? 't' + plantingData.transplanting_pk : plantingData.planting_pk} planting={plantingData} />)
      }
    }
    return (
      <Table>
        <thead>
          <tr key="header">
            <td>
              Plant{' '}
              <a href="#" onClick={this.showNewPlantingAdd}>
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
              <select onChange={this.updateGardenAreaFilter}>{areas}</select>
              <select onChange={this.updateGardenBedFilter}>{beds}</select>
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
}

export { SeedTrayPlantingTable, GardenSquarePlantingTable }
