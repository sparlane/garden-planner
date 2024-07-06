import 'bootstrap'
import 'bootstrap/dist/css/bootstrap.css'

import React from 'react'
import PropTypes from 'prop-types'
import { Table, Button } from 'react-bootstrap'

import $ from 'jquery'

class NewSeedSupplierRow extends React.Component {
  constructor (props) {
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

  updateName (event) {
    const target = event.target
    const value = target.value

    this.setState({ name: value })
  }

  updateWebsite (event) {
    const target = event.target
    const value = target.value

    this.setState({ website: value })
  }

  updateNotes (event) {
    const target = event.target
    const value = target.value

    this.setState({ notes: value })
  }

  add () {
    const data = {
      name: this.state.name,
      notes: this.state.notes
    }
    if (this.state.website !== '' && this.state.website !== null) {
      data['website'] = this.state.website
    }
    $.post('/seeds/supplier/', data, this.props.done())
  }

  render () {
    return (
      <tr>
        <td><input type='text' onChange={this.updateName}/></td>
        <td><input type='text' onChange={this.updateWebsite}/></td>
        <td><textarea onChange={this.updateNotes} /></td>
        <td><Button onClick={this.add}>Add</Button><Button onClick={this.props.done}>Cancel</Button></td>
      </tr>
    )
  }
}
NewSeedSupplierRow.propTypes = {
  done: PropTypes.func.isRequired
}

class SeedSupplierRow extends React.Component {
  render () {
    return (
      <tr>
        <td>{ this.props.supplier.name }</td>
        <td><a href={ this.props.supplier.website }>{ this.props.supplier.website }</a></td>
        <td>{ this.props.supplier.notes }</td>
      </tr>
    )
  }
}
SeedSupplierRow.propTypes = {
  supplier: PropTypes.object.isRequired
}

class SeedSuppliersTable extends React.Component {
  constructor (props) {
    super(props)

    this.state = {
      showSupplierAdd: false,
      suppliers: []
    }

    this.showNewSupplierAdd = this.showNewSupplierAdd.bind(this)
    this.hideNewSupplierAdd = this.hideNewSupplierAdd.bind(this)

    this.updateSupplierList = this.updateSupplierList.bind(this)
  }

  showNewSupplierAdd () {
    this.setState({
      showSupplierAdd: true
    })
  }

  hideNewSupplierAdd () {
    this.setState({
      showSupplierAdd: false
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

  async updateData () {
    await $.getJSON('/seeds/supplier/', this.updateSupplierList)
  }

  render () {
    const rows = []
    if (this.state.showSupplierAdd) {
      rows.push(<NewSeedSupplierRow key='new' done={this.hideNewSupplierAdd} />)
    }
    for (const s in this.state.suppliers) {
      const supplierData = this.state.suppliers[s]
      rows.push((<SeedSupplierRow key={supplierData.pk} supplier={supplierData} />))
    }
    return (
      <Table>
        <thead>
          <tr>
            <td>Name <a href='#' onClick={this.showNewSupplierAdd}>+</a></td>
            <td>Website</td>
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

export { SeedSuppliersTable }