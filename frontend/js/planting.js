import 'bootstrap'
import 'bootstrap/dist/css/bootstrap.css'

import React from 'react'
import PropTypes from 'prop-types'
import { Table, Button } from 'react-bootstrap'

import $ from 'jquery'
import Cookies from 'js-cookie'


class NewSeedTrayPlantingRow extends React.Component {
  constructor (props) {
    super(props)

    this.state = {
      seedPacket: null,
      quantity: 1,
      location: null,
      notes: null
    }

    this.updateSeedPacket = this.updateSeedPacket.bind(this)
    this.updateQuantity = this.updateQuantity.bind(this)
    this.updateLocation = this.updateLocation.bind(this)
    this.updateNotes = this.updateNotes.bind(this)

    this.add = this.add.bind(this)
  }

  updateSeedPacket (event) {
    const target = event.target
    const value = target.value

    this.setState({ seedPacket: value })
  }

  updateQuantity (event) {
    const target = event.target
    const value = target.value

    this.setState({ quantity: value })
  }

  updateLocation (event) {
    const target = event.target
    const value = target.value

    this.setState({ location: value })
  }

  updateNotes (event) {
    const target = event.target
    const value = target.value

    this.setState({ notes: value })
  }

  add () {
    const data = {
      seeds_used: this.state.seedPacket,
      quantity: this.state.quantity,
      location: this.state.location,
      notes: this.state.notes
    }
    $.post('/plantings/seedtray/', data, this.props.done())
  }

  render () {
    const seedPackets = [(<option key='blank'></option>)]
    for (const sp in this.props.seedPackets) {
      const seedPacketData = this.props.seedPackets[sp]
      const seeds = this.props.seeds.find((s) => s.pk === seedPacketData.seeds)
      const supplier = this.props.suppliers.find((s) => s.pk === seeds.supplier)
      const variety = this.props.varieties.find((v) => v.pk === seeds.plant_variety)
      seedPackets.push((<option key={seedPacketData.pk} value={seedPacketData.pk}>{variety.name} from {supplier.name} (Sow By: {seedPacketData.sow_by})</option>))
    }
    return (
      <tr>
        <td><select onChange={this.updateSeedPacket}>{seedPackets}</select></td>
        <td><input type='number' defaultValue={this.state.quantity} onChange={this.updateQuantity}/></td>
        <td></td>
        <td><input type='text' onChange={this.updateLocation} /></td>
        <td><textarea onChange={this.updateNotes} /></td>
        <td><Button onClick={this.add}>Add</Button><Button onClick={this.props.done}>Cancel</Button></td>
      </tr>
    )
  }
}
NewSeedTrayPlantingRow.propTypes = {
  suppliers: PropTypes.array.isRequired,
  varieties: PropTypes.array.isRequired,
  seeds: PropTypes.array.isRequired,
  seedPackets: PropTypes.array.isRequired,
  done: PropTypes.func.isRequired
}

class SeedTrayTransplantingGardenSquareRow extends React.Component {
  constructor (props) {
    super(props)

    this.state = {
      quantity: 1,
      location: null,
      notes: null
    }

    this.updateQuantity = this.updateQuantity.bind(this)
    this.updateLocation = this.updateLocation.bind(this)
    this.updateNotes = this.updateNotes.bind(this)

    this.add = this.add.bind(this)
  }

  updateQuantity (event) {
    const target = event.target
    const value = target.value

    this.setState({ quantity: value })
  }

  updateLocation (event) {
    const target = event.target
    const value = target.value

    this.setState({ location: value })
  }

  updateNotes (event) {
    const target = event.target
    const value = target.value

    this.setState({ notes: value })
  }

  add () {
    const data = {
      original_planting: this.props.planting.pk,
      quantity: this.state.quantity,
      location: this.state.location,
      notes: this.state.notes
    }
    $.post('/plantings/transplantedgardensquare/', data, this.props.done())
  }

  render () {
    const locations = [<option key='blank'></option>]
    for (const b in this.props.gardenBeds) {
      const gardenBedData = this.props.gardenBeds[b]
      const bedSquares = this.props.gardenSquares.filter((s) => s.bed === gardenBedData.pk)
      for (const l in bedSquares) {
        const gardenSquareData = bedSquares[l]
        locations.push(<option key={gardenSquareData.pk} value={gardenSquareData.pk}>{gardenBedData.name} - {gardenSquareData.name}</option>)
      }
    }

    return (
      <tr>
        <td>{ this.props.planting.plant } - { this.props.planting.variety }</td>
        <td><input type='number' defaultValue={this.state.quantity} onChange={this.updateQuantity}/></td>
        <td></td>
        <td><select onChange={this.updateLocation}>{locations}</select></td>
        <td><textarea onChange={this.updateNotes} /></td>
        <td><Button onClick={this.add}>Transplant</Button><Button onClick={this.props.done}>Cancel</Button></td>
      </tr>
    )
  }
}
SeedTrayTransplantingGardenSquareRow.propTypes = {
  planting: PropTypes.object.isRequired,
  gardenBeds: PropTypes.array.isRequired,
  gardenSquares: PropTypes.array.isRequired,
  done: PropTypes.func.isRequired
}


class SeedTrayPlantingRow extends React.Component {
  constructor (props) {
    super (props)

    this.transplant = this.transplant.bind(this)
    this.empty = this.empty.bind(this)
  }

  transplant () {
    this.props.transplantAction(this.props.planting)
  }

  empty () {
    $.ajax({
      url: '/plantings/seedtray/complete/',
      method: 'POST',
      data: {'planting': this.props.planting.pk},
      beforeSend: function(xhr) {
        xhr.setRequestHeader('X-CSRFToken', Cookies.get('csrftoken'))
      }
    })
  }

  render () {
    return (
      <tr>
        <td>{ this.props.planting.plant } - { this.props.planting.variety }</td>
        <td>{ this.props.planting.quantity }</td>
        <td>{ this.props.planting.planted }</td>
        <td>{ this.props.planting.location }</td>
        <td>{ this.props.planting.germination_date_early } - { this.props.planting.germination_date_late }</td>
        <td>{ this.props.planting.notes }</td>
        <td><Button onClick={this.transplant}>Transplant</Button><Button onClick={this.empty}>Empty</Button></td>
      </tr>
    )
  }
}
SeedTrayPlantingRow.propTypes = {
  planting: PropTypes.object.isRequired,
  transplantAction: PropTypes.func.isRequired,
  csrftoken: PropTypes.string.isRequired
}

class SeedTrayPlantingTable extends React.Component {
  constructor (props) {
    super(props)

    this.state = {
      showPlantingAdd: false,
      showTransplanting: null,
      suppliers: [],
      varities: [],
      seeds: [],
      seedPackets: [],
      plantings: []
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
  }

  showNewPlantingAdd () {
    this.setState({
      showPlantingAdd: true
    })
  }

  hideNewPlantingAdd () {
    this.setState({
      showPlantingAdd: false
    })
  }

  showTransplanting (planting) {
    this.setState({
      showTransplanting: planting
    })
  }

  hideTransplanting () {
    this.setState({
      showTransplanting: null
    })
  }

  componentDidMount () {
    this.updateData()
    this.timer = setInterval(() => this.updateData(), 10000)
  }

  componentWillUnmount () {
    clearInterval(this.timer)
    this.timer = null
  }

  updateSupplierList (data) {
    this.setState({
      suppliers: data
    })
  }

  updateVarietiesList (data) {
    this.setState({
      varieties: data
    })
  }

  updateSeedList (data) {
    this.setState({
      seeds: data
    })
  }

  updateSeedPacketList (data) {
    this.setState({
      seedPackets: data
    })
  }

  updatePlantingList (data) {
    this.setState({
      plantings: data.plantings
    })
  }

  updateGardenSquares (data) {
    this.setState({
      gardenSquares: data
    })
  }

  updateGardenBeds (data) {
    this.setState({
      gardenBeds: data
    })
  }

  async updateData () {
    await $.getJSON('/seeds/supplier/', this.updateSupplierList)
    await $.getJSON('/plants/variety/', this.updateVarietiesList)
    await $.getJSON('/seeds/seeds/', this.updateSeedList)
    await $.getJSON('/seeds/packets/', this.updateSeedPacketList)
    await $.getJSON('/plantings/seedtray/current/', this.updatePlantingList)
    await $.getJSON('/garden/squares/', this.updateGardenSquares)
    await $.getJSON('/garden/beds/', this.updateGardenBeds)
  }

  render () {
    const rows = []
    if (this.state.showPlantingAdd) {
      rows.push(<NewSeedTrayPlantingRow key='new' seedPackets={this.state.seedPackets} seeds={this.state.seeds} suppliers={this.state.suppliers} varieties={this.state.varieties} done={this.hideNewPlantingAdd} />)
    }
    for (const p in this.state.plantings) {
      const plantingData = this.state.plantings[p]
      rows.push((<SeedTrayPlantingRow key={plantingData.pk} planting={plantingData} transplantAction={this.showTransplanting} />))
      if (this.state.showTransplanting !== null && this.state.showTransplanting.pk === this.state.plantings[p].pk) {
        rows.push((<SeedTrayTransplantingGardenSquareRow key='transplanting' planting={plantingData} done={this.hideTransplanting} gardenBeds={this.state.gardenBeds} gardenSquares={this.state.gardenSquares} />))
      }
    }
    return (
      <Table>
        <thead>
          <tr>
            <td>Plant <a href='#' onClick={this.showNewPlantingAdd}>+</a></td>
            <td>Quantity</td>
            <td>Date</td>
            <td>Location</td>
            <td>Expected Germination</td>
            <td>Notes</td>
          </tr>
        </thead>
        <tbody>
          {rows}
        </tbody>
      </Table>
    )
  }
}

class NewGardenSquarePlantingRow extends React.Component {
  constructor (props) {
    super(props)

    this.state = {
      seedPacket: null,
      quantity: 1,
      location: null,
      notes: null
    }

    this.updateSeedPacket = this.updateSeedPacket.bind(this)
    this.updateQuantity = this.updateQuantity.bind(this)
    this.updateLocation = this.updateLocation.bind(this)
    this.updateNotes = this.updateNotes.bind(this)

    this.add = this.add.bind(this)
  }

  updateSeedPacket (event) {
    const target = event.target
    const value = target.value

    this.setState({ seedPacket: value })
  }

  updateQuantity (event) {
    const target = event.target
    const value = target.value

    this.setState({ quantity: value })
  }

  updateLocation (event) {
    const target = event.target
    const value = target.value

    this.setState({ location: Number(value) })
  }

  updateNotes (event) {
    const target = event.target
    const value = target.value

    this.setState({ notes: value })
  }

  add () {
    const data = {
      seeds_used: this.state.seedPacket,
      quantity: this.state.quantity,
      location: this.state.location,
      notes: this.state.notes
    }
    $.post('/plantings/directsowgardensquare/', data, this.props.done())
  }

  render () {
    const seedPackets = [(<option key='blank'></option>)]
    for (const sp in this.props.seedPackets) {
      const seedPacketData = this.props.seedPackets[sp]
      const seeds = this.props.seeds.find((s) => s.pk === seedPacketData.seeds)
      const supplier = this.props.suppliers.find((s) => s.pk === seeds.supplier)
      const variety = this.props.varieties.find((v) => v.pk === seeds.plant_variety)
      seedPackets.push((<option key={seedPacketData.pk} value={seedPacketData.pk}>{variety.name} from {supplier.name} (Sow By: {seedPacketData.sow_by})</option>))
    }
    const locations = [<option key='blank'></option>]
    for (const b in this.props.gardenBeds) {
      const gardenBedData = this.props.gardenBeds[b]
      const bedSquares = this.props.gardenSquares.filter((s) => s.bed === gardenBedData.pk)
      for (const l in bedSquares) {
        const gardenSquareData = bedSquares[l]
        locations.push(<option key={gardenSquareData.pk} value={gardenSquareData.pk}>{gardenBedData.name} - {gardenSquareData.name}</option>)
      }
    }
    return (
      <tr>
        <td><select onChange={this.updateSeedPacket}>{seedPackets}</select></td>
        <td><input type='number' defaultValue={this.state.quantity} onChange={this.updateQuantity}/></td>
        <td></td>
        <td><select onChange={this.updateLocation}>{locations}</select></td>
        <td><textarea onChange={this.updateNotes} /></td>
        <td><Button onClick={this.add}>Add</Button><Button onClick={this.props.done}>Cancel</Button></td>
      </tr>
    )
  }
}
NewGardenSquarePlantingRow.propTypes = {
  suppliers: PropTypes.array.isRequired,
  varieties: PropTypes.array.isRequired,
  seeds: PropTypes.array.isRequired,
  seedPackets: PropTypes.array.isRequired,
  done: PropTypes.func.isRequired,
  gardenBeds: PropTypes.array.isRequired,
  gardenSquares: PropTypes.array.isRequired
}

class GardenSquarePlantingRow extends React.Component {
  render () {
    let planted = ''
    if (this.props.planting.transplanted) {
      planted = `${this.props.planting.transplanted} (S: ${this.props.planting.planted})`
    } else {
      planted = this.props.planting.planted
    }
    return (
      <tr>
        <td>{ this.props.planting.plant } - { this.props.planting.variety }</td>
        <td>{ this.props.planting.quantity }</td>
        <td>{ planted }</td>
        <td>{this.props.planting.location.area } - { this.props.planting.location.bed } - { this.props.planting.location.name }</td>
        <td>{ this.props.planting.germination_date_early } - { this.props.planting.germination_date_late }</td>
        <td>{ this.props.planting.maturity_date_early } - { this.props.planting.maturity_date_late }</td>
        <td>{ this.props.planting.notes }</td>
      </tr>
    )
  }
}
GardenSquarePlantingRow.propTypes = {
  suppliers: PropTypes.array.isRequired,
  varieties: PropTypes.array.isRequired,
  seeds: PropTypes.array.isRequired,
  seedPackets: PropTypes.array.isRequired,
  planting: PropTypes.object.isRequired
}

class GardenSquarePlantingTable extends React.Component {
  constructor (props) {
    super(props)

    this.state = {
      showPlantingAdd: false,
      suppliers: [],
      varities: [],
      seeds: [],
      seedPackets: [],
      plantings: [],
      gardenSquares: [],
      gardenBeds: []
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
  }

  showNewPlantingAdd () {
    this.setState({
      showPlantingAdd: true
    })
  }

  hideNewPlantingAdd () {
    this.setState({
      showPlantingAdd: false
    })
  }

  componentDidMount () {
    this.updateData()
    this.timer = setInterval(() => this.updateData(), 10000)
  }

  componentWillUnmount () {
    clearInterval(this.timer)
    this.timer = null
  }

  updateSupplierList (data) {
    this.setState({
      suppliers: data
    })
  }

  updateVarietiesList (data) {
    this.setState({
      varieties: data
    })
  }

  updateSeedList (data) {
    this.setState({
      seeds: data
    })
  }

  updateSeedPacketList (data) {
    this.setState({
      seedPackets: data
    })
  }

  updatePlantingList (data) {
    this.setState({
      plantings: data.plantings
    })
  }

  updateGardenSquares (data) {
    this.setState({
      gardenSquares: data
    })
  }

  updateGardenBeds (data) {
    this.setState({
      gardenBeds: data
    })
  }

  async updateData () {
    await $.getJSON('/seeds/supplier/', this.updateSupplierList)
    await $.getJSON('/plants/variety/', this.updateVarietiesList)
    await $.getJSON('/seeds/seeds/', this.updateSeedList)
    await $.getJSON('/seeds/packets/', this.updateSeedPacketList)
    await $.getJSON('/plantings/garden/squares/current/', this.updatePlantingList)
    await $.getJSON('/garden/squares/', this.updateGardenSquares)
    await $.getJSON('/garden/beds/', this.updateGardenBeds)
  }

  render () {
    const rows = []
    if (this.state.showPlantingAdd) {
      rows.push(<NewGardenSquarePlantingRow key='new' seedPackets={this.state.seedPackets} seeds={this.state.seeds} suppliers={this.state.suppliers} varieties={this.state.varieties} gardenSquares={this.state.gardenSquares} gardenBeds={this.state.gardenBeds}done={this.hideNewPlantingAdd} />)
    }
    for (const p in this.state.plantings) {
      const plantingData = this.state.plantings[p]
      rows.push((<GardenSquarePlantingRow key={plantingData.pk} seedPackets={this.state.seedPackets} seeds={this.state.seeds} suppliers={this.state.suppliers} varieties={this.state.varieties} planting={plantingData} />))
    }
    return (
      <Table>
        <thead>
          <tr>
            <td>Plant <a href='#' onClick={this.showNewPlantingAdd}>+</a></td>
            <td>Quantity</td>
            <td>Date</td>
            <td>Location</td>
            <td>Expected Germination</td>
            <td>Expected Maturity</td>
            <td>Notes</td>
          </tr>
        </thead>
        <tbody>
          {rows}
        </tbody>
      </Table>
    )
  }
}

export { SeedTrayPlantingTable, GardenSquarePlantingTable }