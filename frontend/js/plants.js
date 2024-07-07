import 'bootstrap'
import 'bootstrap/dist/css/bootstrap.css'

import React from 'react'
import PropTypes from 'prop-types'
import { Table, Button } from 'react-bootstrap'

import $ from 'jquery'

class NewPlantFamilyRow extends React.Component {
  constructor (props) {
    super (props)

    this.state = {
      name: '',
      notes: ''
    }

    this.updateName = this.updateName.bind(this)
    this.updateNotes = this.updateNotes.bind(this)
    this.add = this.add.bind(this)
  }

  updateName (event) {
    const target = event.target
    const value = target.value

    this.setState({ name: value })
  }

  updateNotes (event) {
    const target = event.target
    const value = target.value

    this.setState({ notes: value })
  }

  add () {
    $.post('/plants/family/', {
      name: this.state.name,
      notes: this.state.notes
    }, this.props.done())
  }

  render () {
    return (
      <tr>
        <td><input type='text' onChange={this.updateName}/></td>
        <td></td>
        <td></td>
        <td></td>
        <td></td>
        <td></td>
        <td><textarea onChange={this.updateNotes} /></td>
        <td><Button onClick={this.add}>Add</Button><Button onClick={this.props.done}>Cancel</Button></td>
      </tr>
    )
  }
}
NewPlantFamilyRow.propTypes = {
  done: PropTypes.func.isRequired
}

class PlantFamilyRow extends React.Component {
  constructor (props) {
    super(props)

    this.addNewPlant = this.addNewPlant.bind(this)
  }

  addNewPlant () {
    this.props.addNewPlant(this.props.family.pk)
  }

  render () {
    return (
      <tr>
        <td>{this.props.family.name}</td>
        <td><a href='#' onClick={this.addNewPlant}>+</a></td>
        <td></td>
        <td></td>
        <td></td>
        <td></td>
        <td></td>
        <td>{this.props.family.notes}</td>
      </tr>
    )
  }
}
PlantFamilyRow.propTypes = {
  family: PropTypes.object.isRequired,
  addNewPlant: PropTypes.func.isRequired
}

class NewPlantRow extends React.Component {
  constructor (props) {
    super (props)

    this.state = {
      name: '',
      spacing: null,
      row_spacing: null,
      per_square_foot: null,
      notes: ''
    }

    this.updateName = this.updateName.bind(this)
    this.updateNotes = this.updateNotes.bind(this)
    this.updateSpacing = this.updateSpacing.bind(this)
    this.updateRowSpacing = this.updateRowSpacing.bind(this)
    this.updatePerSquareFtRate = this.updatePerSquareFtRate.bind(this)
    this.add = this.add.bind(this)
  }

  updateName (event) {
    const target = event.target
    const value = target.value

    this.setState({ name: value })
  }

  updateSpacing (event) {
    const target = event.target
    const value = target.value

    this.setState({ spacing: value })
  }

  updateRowSpacing (event) {
    const target = event.target
    const value = target.value

    this.setState({ row_spacing: value })
  }

  updatePerSquareFtRate (event) {
    const target = event.target
    const value = target.value

    this.setState({ per_square_foot: value })
  }

  updateNotes (event) {
    const target = event.target
    const value = target.value

    this.setState({ notes: value })
  }

  add () {
    const data = {
      family: this.props.familyId,
      name: this.state.name,
      notes: this.state.notes
    }
    if (this.state.spacing !== '' && this.state.spacing !== null) {
      data['spacing'] = this.state.spacing
    }
    if (this.state.row_spacing !== '' && this.state.row_spacing !== null) {
      data['inter_row_spacing'] = this.state.row_spacing
    }
    if (this.state.per_square_foot !== '' && this.state.per_square_foot !== null) {
      data['plants_per_square_foot'] = this.state.per_square_foot
    }
    $.post('/plants/plant/', data, this.props.done())
  }

  render () {
    return (
      <tr>
        <td>{this.props.familyName}</td>
        <td><input type='text' onChange={this.updateName}/></td>
        <td></td>
        <td><input type='number' onChange={this.updateSpacing}></input></td>
        <td><input type='number' onChange={this.updateRowSpacing}></input></td>
        <td><input type='number' onChange={this.updatePerSquareFtRate}></input></td>
        <td><textarea onChange={this.updateNotes} /></td>
        <td><Button onClick={this.add}>Add</Button><Button onClick={this.props.done}>Cancel</Button></td>
      </tr>
    )
  }
}
NewPlantRow.propTypes = {
  done: PropTypes.func.isRequired,
  familyName: PropTypes.string.isRequired,
  familyId: PropTypes.number.isRequired
}

class PlantRow extends React.Component {
  constructor (props) {
    super(props)

    this.addNewPlantVariety = this.addNewPlantVariety.bind(this)
  }

  addNewPlantVariety () {
    this.props.addNewPlantVariety (this.props.plant.pk)
  }

  render () {
    return (
      <tr>
        <td>{this.props.familyName}</td>
        <td>{this.props.plant.name}</td>
        <td><a href='#' onClick={this.addNewPlantVariety}>+</a></td>
        <td>{this.props.plant.spacing}</td>
        <td>{this.props.plant.row_spacing}</td>
        <td>{this.props.plant.per_square_foot}</td>
        <td>{this.props.plant.notes}</td>
      </tr>
    )
  }
}
PlantRow.propTypes = {
  familyName: PropTypes.string.isRequired,
  plant: PropTypes.object.isRequired,
  addNewPlantVariety: PropTypes.func.isRequired
}

class NewPlantVarietyRow extends React.Component {
  constructor (props) {
    super (props)

    this.state = {
      name: '',
      spacing: null,
      row_spacing: null,
      per_square_foot: null,
      germination_days_min: null,
      germination_days_max: null,
      maturity_days_min: null,
      maturity_days_max: null,
      notes: ''
    }

    this.updateName = this.updateName.bind(this)
    this.updateNotes = this.updateNotes.bind(this)
    this.updateSpacing = this.updateSpacing.bind(this)
    this.updateRowSpacing = this.updateRowSpacing.bind(this)
    this.updatePerSquareFtRate = this.updatePerSquareFtRate.bind(this)
    this.updateGerminationMin = this.updateGerminationMin.bind(this)
    this.updateGerminationMax = this.updateGerminationMax.bind(this)
    this.updateMaturityMin = this.updateMaturityMin.bind(this)
    this.updateMaturityMax = this.updateMaturityMax.bind(this)
    this.add = this.add.bind(this)
  }

  updateName (event) {
    const target = event.target
    const value = target.value

    this.setState({ name: value })
  }

  updateSpacing (event) {
    const target = event.target
    const value = target.value

    this.setState({ spacing: value })
  }

  updateRowSpacing (event) {
    const target = event.target
    const value = target.value

    this.setState({ row_spacing: value })
  }

  updatePerSquareFtRate (event) {
    const target = event.target
    const value = target.value

    this.setState({ per_square_foot: value })
  }

  updateGerminationMin (event) {
    const target = event.target
    const value = target.value

    this.setState({ germination_days_min: value })
  }

  updateGerminationMax (event) {
    const target = event.target
    const value = target.value

    this.setState({ germination_days_max: value })
  }

  updateMaturityMin (event) {
    const target = event.target
    const value = target.value

    this.setState({ maturity_days_min: value })
  }

  updateMaturityMax (event) {
    const target = event.target
    const value = target.value

    this.setState({ maturity_days_max: value })
  }

  updateNotes (event) {
    const target = event.target
    const value = target.value

    this.setState({ notes: value })
  }

  add () {
    const data = {
      plant: this.props.plantId,
      name: this.state.name,
      notes: this.state.notes
    }
    if (this.state.spacing !== '' && this.state.spacing !== null) {
      data['spacing'] = this.state.spacing
    }
    if (this.state.row_spacing !== '' && this.state.row_spacing !== null) {
      data['inter_row_spacing'] = this.state.row_spacing
    }
    if (this.state.per_square_foot !== '' && this.state.per_square_foot !== null) {
      data['plants_per_square_foot'] = this.state.per_square_foot
    }
    if (this.state.germination_days_min !== '' && this.state.germination_days_min !== null) {
      data['germination_days_min'] = this.state.germination_days_min
    }
    if (this.state.germination_days_max !== '' && this.state.germination_days_max !== null) {
      data['germination_days_max'] = this.state.germination_days_max
    }
    if (this.state.maturity_days_min !== '' && this.state.maturity_days_min !== null) {
      data['maturity_days_min'] = this.state.maturity_days_min
    }
    if (this.state.maturity_days_max !== '' && this.state.maturity_days_max !== null) {
      data['maturity_days_max'] = this.state.maturity_days_max
    }
    $.post('/plants/variety/', data, this.props.done())
  }

  render () {
    return (
      <tr>
        <td>{this.props.familyName}</td>
        <td>{this.props.plantName}</td>
        <td><input type='text' onChange={this.updateName}/></td>
        <td><input type='number' onChange={this.updateSpacing}></input></td>
        <td><input type='number' onChange={this.updateRowSpacing}></input></td>
        <td><input type='number' onChange={this.updatePerSquareFtRate}></input></td>
        <td><input type='number' onChange={this.updateGerminationMin} /> - <input type='number' onChange={this.updateGerminationMax} /></td>
        <td><input type='number' onChange={this.updateMaturityMin} /> - <input type='number' onChange={this.updateMaturityMax} /></td>
        <td><textarea onChange={this.updateNotes} /></td>
        <td><Button onClick={this.add}>Add</Button><Button onClick={this.props.done}>Cancel</Button></td>
      </tr>
    )
  }
}
NewPlantVarietyRow.propTypes = {
  done: PropTypes.func.isRequired,
  familyName: PropTypes.string.isRequired,
  plantName: PropTypes.string.isRequired,
  plantId: PropTypes.number.isRequired
}

class PlantVarietyRow extends React.Component {
  render () {
    return (
      <tr>
        <td>{this.props.familyName}</td>
        <td>{this.props.plantName}</td>
        <td>{this.props.variety.name}</td>
        <td>{this.props.variety.spacing}</td>
        <td>{this.props.variety.inter_row_spacing}</td>
        <td>{this.props.variety.plants_per_square_foot}</td>
        <td>{this.props.variety.germination_days_min}-{this.props.variety.germination_days_max}</td>
        <td>{this.props.variety.maturity_days_min}-{this.props.variety.maturity_days_max}</td>
        <td>{this.props.variety.notes}</td>
      </tr>
    )
  }
}
PlantVarietyRow.propTypes = {
  variety: PropTypes.object.isRequired,
  familyName: PropTypes.string.isRequired,
  plantName: PropTypes.string.isRequired
}

class PlantsView extends React.Component {
  constructor (props) {
    super(props)

    this.state = {
      'showFamilyAdd': false,
      'showPlantAdd': null,
      'showVarietyAdd': null,
      'families': [],
      'plants': [],
      'varieties': [],
    }

    this.showNewFamilyAdd = this.showNewFamilyAdd.bind(this)
    this.hideNewFamilyAdd = this.hideNewFamilyAdd.bind(this)

    this.showNewPlantAdd = this.showNewPlantAdd.bind(this)
    this.hideNewPlantAdd = this.hideNewPlantAdd.bind(this)

    this.showNewVarietyAdd = this.showNewVarietyAdd.bind(this)
    this.hideNewVarietyAdd = this.hideNewVarietyAdd.bind(this)

    this.updatePlantFamilyResponse = this.updatePlantFamilyResponse.bind(this)
    this.updatePlantResponse = this.updatePlantResponse.bind(this)
    this.updatePlantVarietiesResponse = this.updatePlantVarietiesResponse.bind(this)
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

  showNewFamilyAdd () {
    this.setState({
      showFamilyAdd: true
    })
  }

  hideNewFamilyAdd () {
    this.setState({
      showFamilyAdd: false
    })
  }

  showNewPlantAdd (familyId) {
    this.setState({
      showPlantAdd: familyId
    })
  }

  hideNewPlantAdd () {
    this.setState({
      showPlantAdd: null
    })
  }

  showNewVarietyAdd (plantId) {
    this.setState({
      showVarietyAdd: plantId
    })
  }

  hideNewVarietyAdd () {
    this.setState({
      showPlantAdd: null
    })
  }

  updatePlantFamilyResponse (data) {
    this.setState({
      families: data,
    })
  }

  updatePlantResponse (data) {
    this.setState({
      plants: data,
    })
  }

  updatePlantVarietiesResponse (data) {
    this.setState({
      varieties: data,
    })
  }

  async updateData () {
    await $.getJSON('/plants/family/', this.updatePlantFamilyResponse)
    await $.getJSON('/plants/plant/', this.updatePlantResponse)
    await $.getJSON('/plants/variety/', this.updatePlantVarietiesResponse)
  }

  render () {
    const rows = []
    if (this.state.showFamilyAdd) {
      rows.push(<NewPlantFamilyRow done={this.hideNewFamilyAdd} key='family-add' />)
    }
    for (const f in this.state.families) {
      const familyData = this.state.families[f]
      rows.push((<PlantFamilyRow family={familyData} key={'family-' + familyData.pk} addNewPlant={this.showNewPlantAdd} />))
      if (this.state.showPlantAdd === familyData.pk) {
        rows.push(<NewPlantRow done={this.hideNewPlantAdd} familyId={familyData.pk} familyName={familyData.name} key='plant-add' />)
      }
      const plants = this.state.plants.filter((plant) => plant.family === familyData.pk)
      for (const p in plants) {
        const plantData = plants[p]
        rows.push(<PlantRow familyName={familyData.name} plant={plantData} key={'plant-' + plantData.pk} addNewPlantVariety={this.showNewVarietyAdd} />)
        if (this.state.showVarietyAdd === plantData.pk) {
          rows.push(<NewPlantVarietyRow done={this.hideNewVarietyAdd} plantId={plantData.pk} familyName={familyData.name} plantName={plantData.name} key='variety-add' />)
        }
        const varieties = this.state.varieties.filter((variety) => variety.plant === plantData.pk)
        for (const v in varieties) {
          const varietyData = varieties[v]
          rows.push(<PlantVarietyRow variety={varietyData} familyName={familyData.name} plantName={plantData.name} key={'variety-' + varietyData.pk} />)
        }
      }
    }
    return (
      <Table>
        <thead>
          <tr>
            <td>Family <a href='#' onClick={this.showNewFamilyAdd}>+</a></td>
            <td>Plant</td>
            <td>Variety</td>
            <td>Spacing (mm)</td>
            <td>Row Spacing (mm)</td>
            <td>per sq/ft</td>
            <td>Germination (days)</td>
            <td>Maturity (days)</td>
            <td>Notes</td>
          </tr>
        </thead>
        <tbody>
          { rows }
        </tbody>
      </Table>
    )
  }
}

export { PlantsView }