import 'bootstrap'
import 'bootstrap/dist/css/bootstrap.css'

import React from 'react'
import PropTypes from 'prop-types'
import { Table, Button } from 'react-bootstrap'

import $ from 'jquery'

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

class SeedTrayPlantingRow extends React.Component {
  render () {
    const seedPacket = this.props.seedPackets.find((sp) => sp.pk === this.props.planting.seeds_used)
    const seeds = this.props.seeds.find((s) => s.pk === seedPacket.seeds)
    const variety = this.props.varieties.find((v) => v.pk === seeds.plant_variety)
    return (
      <tr>
        <td>{ variety.name }</td>
        <td>{ this.props.planting.quantity }</td>
        <td>{ this.props.planting.planted }</td>
        <td>{ this.props.planting.location }</td>
        <td>{ this.props.planting.notes }</td>
      </tr>
    )
  }
}
SeedTrayPlantingRow.propTypes = {
  suppliers: PropTypes.array.isRequired,
  varieties: PropTypes.array.isRequired,
  seeds: PropTypes.array.isRequired,
  seedPackets: PropTypes.array.isRequired,
  planting: PropTypes.object.isRequired
}

class SeedTrayPlantingTable extends React.Component {
  constructor (props) {
    super(props)

    this.state = {
      showPlantingAdd: false,
      suppliers: [],
      varities: [],
      seeds: [],
      seedPackets: [],
      plantings: []
    }

    this.showNewPlantingAdd = this.showNewPlantingAdd.bind(this)
    this.hideNewPlantingAdd = this.hideNewPlantingAdd.bind(this)

    this.updateData = this.updateData.bind(this)
    this.updateSupplierList = this.updateSupplierList.bind(this)
    this.updateVarietiesList = this.updateVarietiesList.bind(this)
    this.updateSeedList = this.updateSeedList.bind(this)
    this.updateSeedPacketList = this.updateSeedPacketList.bind(this)
    this.updatePlantingList = this.updatePlantingList.bind(this)
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
    $.ajaxSetup({ timeout: 2500 })
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
      plantings: data
    })
  }

  async updateData () {
    await $.getJSON('/seeds/supplier/', this.updateSupplierList)
    await $.getJSON('/plants/variety/', this.updateVarietiesList)
    await $.getJSON('/seeds/seeds/', this.updateSeedList)
    await $.getJSON('/seeds/packets/', this.updateSeedPacketList)
    await $.getJSON('/plantings/seedtray/', this.updatePlantingList)
  }

  render () {
    const rows = []
    if (this.state.showPlantingAdd) {
      rows.push(<NewSeedTrayPlantingRow key='new' seedPackets={this.state.seedPackets} seeds={this.state.seeds} suppliers={this.state.suppliers} varieties={this.state.varieties} done={this.hideNewPlantingAdd} />)
    }
    for (const p in this.state.plantings) {
      const plantingData = this.state.plantings[p]
      rows.push((<SeedTrayPlantingRow key={plantingData.pk} seedPackets={this.state.seedPackets} seeds={this.state.seeds} suppliers={this.state.suppliers} varieties={this.state.varieties} planting={plantingData} />))
    }
    return (
      <Table>
        <thead>
          <tr>
            <td>Variety <a href='#' onClick={this.showNewPlantingAdd}>+</a></td>
            <td>Quantity</td>
            <td>Date</td>
            <td>Location</td>
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

export { SeedTrayPlantingTable }