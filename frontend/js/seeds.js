import 'bootstrap'
import 'bootstrap/dist/css/bootstrap.css'

import React from 'react'
import PropTypes from 'prop-types'
import { Table, Button } from 'react-bootstrap'

import $ from 'jquery'
import Cookies from 'js-cookie'

class NewSeedSupplierRow extends React.Component {
  constructor(props) {
    super(props)

    this.state = {
      name: '',
      website: null,
      notes: null
    }

    this.updateName = this.updateName.bind(this)
    this.updateWebsite = this.updateWebsite.bind(this)
    this.updateNotes = this.updateNotes.bind(this)

    this.add = this.add.bind(this)
  }

  updateName(event) {
    const target = event.target
    const value = target.value

    this.setState({ name: value })
  }

  updateWebsite(event) {
    const target = event.target
    const value = target.value

    this.setState({ website: value })
  }

  updateNotes(event) {
    const target = event.target
    const value = target.value

    this.setState({ notes: value })
  }

  add() {
    const data = {
      name: this.state.name,
      notes: this.state.notes
    }
    if (this.state.website !== '' && this.state.website !== null) {
      data['website'] = this.state.website
    }
    $.post('/seeds/supplier/', data, this.props.done())
  }

  render() {
    return (
      <tr>
        <td>
          <input type="text" onChange={this.updateName} />
        </td>
        <td>
          <input type="text" onChange={this.updateWebsite} />
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
NewSeedSupplierRow.propTypes = {
  done: PropTypes.func.isRequired
}

class SeedSupplierRow extends React.Component {
  render() {
    return (
      <tr>
        <td>{this.props.supplier.name}</td>
        <td>
          <a href={this.props.supplier.website}>{this.props.supplier.website}</a>
        </td>
        <td>{this.props.supplier.notes}</td>
      </tr>
    )
  }
}
SeedSupplierRow.propTypes = {
  supplier: PropTypes.object.isRequired
}

class SeedSuppliersTable extends React.Component {
  constructor(props) {
    super(props)

    this.state = {
      showSupplierAdd: false,
      suppliers: []
    }

    this.showNewSupplierAdd = this.showNewSupplierAdd.bind(this)
    this.hideNewSupplierAdd = this.hideNewSupplierAdd.bind(this)

    this.updateSupplierList = this.updateSupplierList.bind(this)
  }

  showNewSupplierAdd() {
    this.setState({
      showSupplierAdd: true
    })
  }

  hideNewSupplierAdd() {
    this.setState({
      showSupplierAdd: false
    })
  }

  componentDidMount() {
    this.updateData()
    this.timer = setInterval(() => this.updateData(), 10000)
  }

  componentWillUnmount() {
    clearInterval(this.timer)
    this.timer = null
  }

  updateSupplierList(data) {
    this.setState({
      suppliers: data
    })
  }

  async updateData() {
    await $.getJSON('/seeds/supplier/', this.updateSupplierList)
  }

  render() {
    const rows = []
    if (this.state.showSupplierAdd) {
      rows.push(<NewSeedSupplierRow key="new" done={this.hideNewSupplierAdd} />)
    }
    for (const s in this.state.suppliers) {
      const supplierData = this.state.suppliers[s]
      rows.push(<SeedSupplierRow key={supplierData.pk} supplier={supplierData} />)
    }
    return (
      <Table>
        <thead>
          <tr>
            <td>
              Name{' '}
              <a href="#" onClick={this.showNewSupplierAdd}>
                +
              </a>
            </td>
            <td>Website</td>
            <td>Notes</td>
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </Table>
    )
  }
}

class NewSeedRow extends React.Component {
  constructor(props) {
    super(props)

    this.state = {
      supplier: null,
      variety: null,
      supplierCode: null,
      website: null,
      notes: null
    }

    this.updateSupplier = this.updateSupplier.bind(this)
    this.updateVariety = this.updateVariety.bind(this)
    this.updateSupplierCode = this.updateSupplierCode.bind(this)
    this.updateWebsite = this.updateWebsite.bind(this)
    this.updateNotes = this.updateNotes.bind(this)

    this.add = this.add.bind(this)
  }

  updateSupplier(event) {
    const target = event.target
    const value = target.value

    this.setState({ supplier: value })
  }

  updateVariety(event) {
    const target = event.target
    const value = target.value

    this.setState({ variety: value })
  }

  updateSupplierCode(event) {
    const target = event.target
    const value = target.value

    this.setState({ supplierCode: value })
  }

  updateWebsite(event) {
    const target = event.target
    const value = target.value

    this.setState({ website: value })
  }

  updateNotes(event) {
    const target = event.target
    const value = target.value

    this.setState({ notes: value })
  }

  add() {
    let supplier = this.state.supplier
    if (supplier === null || supplier === '') {
      supplier = this.props.suppliers[0].pk
    }
    let variety = this.state.variety
    if (variety === null || variety === '') {
      variety = this.props.varieties[0].pk
    }
    const data = {
      supplier: supplier,
      plant_variety: variety,
      notes: this.state.notes
    }
    if (this.state.supplierCode !== '' && this.state.supplierCode !== null) {
      data['supplier_code'] = this.state.supplierCode
    }
    if (this.state.website !== '' && this.state.website !== null) {
      data['url'] = this.state.website
    }
    $.post('/seeds/seeds/', data, this.props.done())
  }

  render() {
    const suppliers = []
    for (const s in this.props.suppliers) {
      const supplierData = this.props.suppliers[s]
      suppliers.push(
        <option key={supplierData.pk} value={supplierData.pk}>
          {supplierData.name}
        </option>
      )
    }
    const varieties = []
    for (const v in this.props.varieties) {
      const varietyData = this.props.varieties[v]
      varieties.push(
        <option key={varietyData.pk} value={varietyData.pk}>
          {varietyData.name}
        </option>
      )
    }
    return (
      <tr>
        <td>
          <select onChange={this.updateSupplier}>{suppliers}</select>
        </td>
        <td>
          <select onChange={this.updateVariety}>{varieties}</select>
        </td>
        <td>
          <input type="text" onChange={this.updateSupplierCode} />
        </td>
        <td>
          <input type="text" onChange={this.updateWebsite} />
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
NewSeedRow.propTypes = {
  suppliers: PropTypes.array.isRequired,
  varieties: PropTypes.array.isRequired,
  done: PropTypes.func.isRequired
}

class SeedRow extends React.Component {
  render() {
    const supplier = this.props.suppliers.find((s) => s.pk == this.props.seed.supplier)
    const variety = this.props.varieties.find((v) => v.pk === this.props.seed.plant_variety)
    return (
      <tr>
        <td>{supplier.name}</td>
        <td>{variety.name}</td>
        <td>{this.props.seed.supplier_code}</td>
        <td>
          <a href={this.props.seed.url}>{this.props.seed.url}</a>
        </td>
        <td>{this.props.seed.notes}</td>
      </tr>
    )
  }
}
SeedRow.propTypes = {
  suppliers: PropTypes.array.isRequired,
  varieties: PropTypes.array.isRequired,
  seed: PropTypes.object.isRequired
}

class SeedTable extends React.Component {
  constructor(props) {
    super(props)

    this.state = {
      showSeedAdd: false,
      suppliers: [],
      varieties: [],
      seeds: []
    }

    this.showNewSeedAdd = this.showNewSeedAdd.bind(this)
    this.hideNewSeedAdd = this.hideNewSeedAdd.bind(this)

    this.updateData = this.updateData.bind(this)
    this.updateSupplierList = this.updateSupplierList.bind(this)
    this.updateVarietiesList = this.updateVarietiesList.bind(this)
    this.updateSeedList = this.updateSeedList.bind(this)
  }

  showNewSeedAdd() {
    this.setState({
      showSeedAdd: true
    })
  }

  hideNewSeedAdd() {
    this.setState({
      showSeedAdd: false
    })
  }

  componentDidMount() {
    this.updateData()
    this.timer = setInterval(() => this.updateData(), 10000)
  }

  componentWillUnmount() {
    clearInterval(this.timer)
    this.timer = null
  }

  updateSupplierList(data) {
    this.setState({
      suppliers: data
    })
  }

  updateVarietiesList(data) {
    this.setState({
      varieties: data
    })
  }

  updateSeedList(data) {
    this.setState({
      seeds: data
    })
  }

  async updateData() {
    await $.getJSON('/seeds/supplier/', this.updateSupplierList)
    await $.getJSON('/plants/variety/', this.updateVarietiesList)
    await $.getJSON('/seeds/seeds/', this.updateSeedList)
  }

  render() {
    const rows = []
    if (this.state.showSeedAdd) {
      rows.push(<NewSeedRow key="new" suppliers={this.state.suppliers} varieties={this.state.varieties} done={this.hideNewSeedAdd} />)
    }
    for (const s in this.state.seeds) {
      const seedData = this.state.seeds[s]
      rows.push(<SeedRow key={seedData.pk} suppliers={this.state.suppliers} varieties={this.state.varieties} seed={seedData} />)
    }
    return (
      <Table>
        <thead>
          <tr>
            <td>Supplier</td>
            <td>Variety</td>
            <td>Supplier Code</td>
            <td>Link</td>
            <td>Notes</td>
            <td>
              <a href="#" onClick={this.showNewSeedAdd}>
                +
              </a>
            </td>
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </Table>
    )
  }
}

class NewSeedPacketRow extends React.Component {
  constructor(props) {
    super(props)

    this.state = {
      seeds: null,
      purchaseDate: null,
      sowBy: null,
      notes: null
    }

    this.updateSeeds = this.updateSeeds.bind(this)
    this.updatePurchaseDate = this.updatePurchaseDate.bind(this)
    this.updateSowBy = this.updateSowBy.bind(this)
    this.updateNotes = this.updateNotes.bind(this)

    this.add = this.add.bind(this)
  }

  updateSeeds(event) {
    const target = event.target
    const value = target.value

    this.setState({ seeds: value })
  }

  updatePurchaseDate(event) {
    const target = event.target
    const value = target.value

    this.setState({ purchaseDate: value })
  }

  updateSowBy(event) {
    const target = event.target
    const value = target.value

    this.setState({ sowBy: value })
  }

  updateNotes(event) {
    const target = event.target
    const value = target.value

    this.setState({ notes: value })
  }

  add() {
    let seeds = this.state.seeds
    if (seeds === null || seeds === '') {
      seeds = this.props.seeds[0].pk
    }
    const data = {
      seeds: seeds,
      notes: this.state.notes
    }
    if (this.state.purchaseDate !== '' && this.state.purchaseDate !== null) {
      data['purchase_date'] = this.state.purchaseDate
    }
    if (this.state.sowBy !== '' && this.state.sowBy !== null) {
      data['sow_by'] = this.state.sowBy
    }
    $.post('/seeds/packets/', data, this.props.done())
  }

  render() {
    const seeds = []
    for (const s in this.props.seeds) {
      const seedsData = this.props.seeds[s]
      const supplier = this.props.suppliers.find((s) => s.pk === seedsData.supplier)
      const variety = this.props.varieties.find((v) => v.pk === seedsData.plant_variety)
      seeds.push(
        <option key={seedsData.pk} value={seedsData.pk}>
          {variety.name} from {supplier.name}
        </option>
      )
    }
    return (
      <tr>
        <td>
          <select onChange={this.updateSeeds}>{seeds}</select>
        </td>
        <td>
          <input type="text" onChange={this.updatePurchaseDate} />
        </td>
        <td>
          <input type="text" onChange={this.updateSowBy} />
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
NewSeedPacketRow.propTypes = {
  suppliers: PropTypes.array.isRequired,
  varieties: PropTypes.array.isRequired,
  seeds: PropTypes.array.isRequired,
  done: PropTypes.func.isRequired
}

class SeedPacketRow extends React.Component {
  constructor(props) {
    super(props)

    this.empty = this.empty.bind(this)
  }

  empty() {
    $.ajax({
      url: '/seeds/packets/empty/',
      method: 'POST',
      data: { packet: this.props.seedPacket.pk },
      beforeSend: function (xhr) {
        xhr.setRequestHeader('X-CSRFToken', Cookies.get('csrftoken'))
      }
    })
  }

  render() {
    const seed = this.props.seeds.find((s) => s.pk === this.props.seedPacket.seeds)
    const supplier = this.props.suppliers.find((s) => s.pk == seed.supplier)
    const variety = this.props.varieties.find((v) => v.pk === seed.plant_variety)
    return (
      <tr>
        <td>
          {variety.name} from {supplier.name}
        </td>
        <td>{this.props.seedPacket.purchase_date}</td>
        <td>{this.props.seedPacket.sow_by}</td>
        <td>{this.props.seedPacket.notes}</td>
        <td>
          <Button onClick={this.empty}>Empty</Button>
        </td>
      </tr>
    )
  }
}
SeedPacketRow.propTypes = {
  suppliers: PropTypes.array.isRequired,
  varieties: PropTypes.array.isRequired,
  seeds: PropTypes.array.isRequired,
  seedPacket: PropTypes.object.isRequired
}

class SeedStockTable extends React.Component {
  constructor(props) {
    super(props)

    this.state = {
      showSeedPacketAdd: false,
      suppliers: [],
      varieties: [],
      seeds: [],
      seedPackets: []
    }

    this.showNewSeedPacketAdd = this.showNewSeedPacketAdd.bind(this)
    this.hideNewSeedPacketAdd = this.hideNewSeedPacketAdd.bind(this)

    this.updateData = this.updateData.bind(this)
    this.updateSupplierList = this.updateSupplierList.bind(this)
    this.updateVarietiesList = this.updateVarietiesList.bind(this)
    this.updateSeedList = this.updateSeedList.bind(this)
    this.updateSeedPacketList = this.updateSeedPacketList.bind(this)
  }

  showNewSeedPacketAdd() {
    this.setState({
      showSeedPacketAdd: true
    })
  }

  hideNewSeedPacketAdd() {
    this.setState({
      showSeedPacketAdd: false
    })
  }

  componentDidMount() {
    this.updateData()
    this.timer = setInterval(() => this.updateData(), 10000)
  }

  componentWillUnmount() {
    clearInterval(this.timer)
    this.timer = null
  }

  updateSupplierList(data) {
    this.setState({
      suppliers: data
    })
  }

  updateVarietiesList(data) {
    this.setState({
      varieties: data
    })
  }

  updateSeedList(data) {
    this.setState({
      seeds: data
    })
  }

  updateSeedPacketList(data) {
    this.setState({
      seedPackets: data
    })
  }

  async updateData() {
    await $.getJSON('/seeds/supplier/', this.updateSupplierList)
    await $.getJSON('/plants/variety/', this.updateVarietiesList)
    await $.getJSON('/seeds/seeds/', this.updateSeedList)
    await $.getJSON('/seeds/packets/', this.updateSeedPacketList)
  }

  render() {
    const rows = []
    if (this.state.showSeedPacketAdd) {
      rows.push(<NewSeedPacketRow key="new" suppliers={this.state.suppliers} varieties={this.state.varieties} seeds={this.state.seeds} done={this.hideNewSeedPacketAdd} />)
    }
    for (const s in this.state.seedPackets) {
      const seedPacketData = this.state.seedPackets[s]
      rows.push(<SeedPacketRow key={seedPacketData.pk} suppliers={this.state.suppliers} varieties={this.state.varieties} seeds={this.state.seeds} seedPacket={seedPacketData} />)
    }
    return (
      <Table>
        <thead>
          <tr>
            <td>Seeds</td>
            <td>Purchase Date</td>
            <td>Sow By</td>
            <td>Notes</td>
            <td>
              <a href="#" onClick={this.showNewSeedPacketAdd}>
                +
              </a>
            </td>
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </Table>
    )
  }
}

export { SeedSuppliersTable, SeedTable, SeedStockTable }
