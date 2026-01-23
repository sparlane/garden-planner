import 'bootstrap'
import 'bootstrap/dist/css/bootstrap.css'

import React from 'react'
import { Table, Button } from 'react-bootstrap'

import { Plant, PlantCreate, PlantFamily, PlantVariety, PlantVarietyCreate } from './types/plants'
import { addPlant, addPlantFamily, addPlantVariety, getPlantFamilies, getPlants, getPlantVarieties } from './api/plants'

interface NewPlantFamilyRowProps {
  done: () => void
}

interface NewPlantFamilyRowState {
  name: string
  notes: string
}

class NewPlantFamilyRow extends React.Component<NewPlantFamilyRowProps, NewPlantFamilyRowState> {
  constructor(props: NewPlantFamilyRowProps) {
    super(props)

    this.state = {
      name: '',
      notes: ''
    }

    this.updateName = this.updateName.bind(this)
    this.updateNotes = this.updateNotes.bind(this)
    this.add = this.add.bind(this)
  }

  updateName(event: React.ChangeEvent<HTMLInputElement>) {
    const { value } = event.target

    this.setState({ name: value })
  }

  updateNotes(event: React.ChangeEvent<HTMLTextAreaElement>) {
    const { value } = event.target

    this.setState({ notes: value })
  }

  add() {
    addPlantFamily({
      name: this.state.name,
      notes: this.state.notes
    }).done(this.props.done)
  }

  render() {
    return (
      <tr>
        <td>
          <input type="text" onChange={this.updateName} />
        </td>
        <td></td>
        <td></td>
        <td></td>
        <td></td>
        <td></td>
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

interface PlantFamilyRowProps {
  family: PlantFamily
  addNewPlant: (familyId: number) => void
}

class PlantFamilyRow extends React.Component<PlantFamilyRowProps> {
  constructor(props: PlantFamilyRowProps) {
    super(props)

    this.addNewPlant = this.addNewPlant.bind(this)
  }

  addNewPlant() {
    this.props.addNewPlant(this.props.family.pk)
  }

  render() {
    return (
      <tr>
        <td>{this.props.family.name}</td>
        <td>
          <a href="#" onClick={this.addNewPlant}>
            +
          </a>
        </td>
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

interface NewPlantRowProps {
  done: () => void
  familyName: string
  familyId: number
}

interface NewPlantRowState {
  name: string
  spacing?: number
  row_spacing?: number
  per_square_foot?: number
  notes: string
}

class NewPlantRow extends React.Component<NewPlantRowProps, NewPlantRowState> {
  constructor(props: NewPlantRowProps) {
    super(props)

    this.state = {
      name: '',
      spacing: undefined,
      row_spacing: undefined,
      per_square_foot: undefined,
      notes: ''
    }

    this.updateName = this.updateName.bind(this)
    this.updateNotes = this.updateNotes.bind(this)
    this.updateSpacing = this.updateSpacing.bind(this)
    this.updateRowSpacing = this.updateRowSpacing.bind(this)
    this.updatePerSquareFtRate = this.updatePerSquareFtRate.bind(this)
    this.add = this.add.bind(this)
  }

  updateName(event: React.ChangeEvent<HTMLInputElement>) {
    const { value } = event.target

    this.setState({ name: value })
  }

  updateSpacing(event: React.ChangeEvent<HTMLInputElement>) {
    const { value } = event.target

    if (value === '' || value === undefined || value === null) {
      this.setState({ spacing: undefined })
      return
    }

    this.setState({ spacing: Number(value) })
  }

  updateRowSpacing(event: React.ChangeEvent<HTMLInputElement>) {
    const { value } = event.target

    if (value === '' || value === undefined || value === null) {
      this.setState({ row_spacing: undefined })
      return
    }
    this.setState({ row_spacing: Number(value) })
  }

  updatePerSquareFtRate(event: React.ChangeEvent<HTMLInputElement>) {
    const { value } = event.target

    if (value === '' || value === undefined || value === null) {
      this.setState({ per_square_foot: undefined })
      return
    }
    this.setState({ per_square_foot: Number(value) })
  }

  updateNotes(event: React.ChangeEvent<HTMLTextAreaElement>) {
    const { value } = event.target

    this.setState({ notes: value })
  }

  add() {
    const data: PlantCreate = {
      family: this.props.familyId,
      name: this.state.name,
      notes: this.state.notes
    }
    if (this.state.spacing !== undefined) {
      data.spacing = this.state.spacing
    }
    if (this.state.row_spacing !== undefined) {
      data.inter_row_spacing = this.state.row_spacing
    }
    if (this.state.per_square_foot !== undefined) {
      data.plants_per_square_foot = this.state.per_square_foot
    }
    addPlant(data).done(this.props.done)
  }

  render() {
    return (
      <tr>
        <td>{this.props.familyName}</td>
        <td>
          <input type="text" onChange={this.updateName} />
        </td>
        <td></td>
        <td>
          <input type="number" onChange={this.updateSpacing}></input>
        </td>
        <td>
          <input type="number" onChange={this.updateRowSpacing}></input>
        </td>
        <td>
          <input type="number" onChange={this.updatePerSquareFtRate}></input>
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

interface PlantRowProps {
  familyName: string
  plant: Plant
  addNewPlantVariety: (plantId: number) => void
}

class PlantRow extends React.Component<PlantRowProps> {
  constructor(props: PlantRowProps) {
    super(props)

    this.addNewPlantVariety = this.addNewPlantVariety.bind(this)
  }

  addNewPlantVariety() {
    this.props.addNewPlantVariety(this.props.plant.pk)
  }

  render() {
    return (
      <tr>
        <td>{this.props.familyName}</td>
        <td>{this.props.plant.name}</td>
        <td>
          <a href="#" onClick={this.addNewPlantVariety}>
            +
          </a>
        </td>
        <td>{this.props.plant.spacing}</td>
        <td>{this.props.plant.inter_row_spacing}</td>
        <td>{this.props.plant.plants_per_square_foot}</td>
        <td>{this.props.plant.notes}</td>
      </tr>
    )
  }
}

interface NewPlantVarietyRowProps {
  done: () => void
  familyName: string
  plantName: string
  plantId: number
}

interface NewPlantVarietyRowState {
  name: string
  spacing?: number
  row_spacing?: number
  per_square_foot?: number
  germination_days_min?: number
  germination_days_max?: number
  maturity_days_min?: number
  maturity_days_max?: number
  notes: string
}

class NewPlantVarietyRow extends React.Component<NewPlantVarietyRowProps, NewPlantVarietyRowState> {
  constructor(props: NewPlantVarietyRowProps) {
    super(props)

    this.state = {
      name: '',
      spacing: undefined,
      row_spacing: undefined,
      per_square_foot: undefined,
      germination_days_min: undefined,
      germination_days_max: undefined,
      maturity_days_min: undefined,
      maturity_days_max: undefined,
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

  updateName(event: React.ChangeEvent<HTMLInputElement>) {
    const { value } = event.target

    this.setState({ name: value })
  }

  updateSpacing(event: React.ChangeEvent<HTMLInputElement>) {
    const { value } = event.target

    if (value === '' || value === undefined || value === null) {
      this.setState({ spacing: undefined })
      return
    }

    this.setState({ spacing: Number(value) })
  }

  updateRowSpacing(event: React.ChangeEvent<HTMLInputElement>) {
    const { value } = event.target

    if (value === '' || value === undefined || value === null) {
      this.setState({ row_spacing: undefined })
      return
    }

    this.setState({ row_spacing: Number(value) })
  }

  updatePerSquareFtRate(event: React.ChangeEvent<HTMLInputElement>) {
    const { value } = event.target

    if (value === '' || value === undefined || value === null) {
      this.setState({ per_square_foot: undefined })
      return
    }

    this.setState({ per_square_foot: Number(value) })
  }

  updateGerminationMin(event: React.ChangeEvent<HTMLInputElement>) {
    const { value } = event.target

    if (value === '' || value === undefined || value === null) {
      this.setState({ germination_days_min: undefined })
      return
    }

    this.setState({ germination_days_min: Number(value) })
  }

  updateGerminationMax(event: React.ChangeEvent<HTMLInputElement>) {
    const { value } = event.target

    if (value === '' || value === undefined || value === null) {
      this.setState({ germination_days_max: undefined })
      return
    }

    this.setState({ germination_days_max: Number(value) })
  }

  updateMaturityMin(event: React.ChangeEvent<HTMLInputElement>) {
    const { value } = event.target

    if (value === '' || value === undefined || value === null) {
      this.setState({ maturity_days_min: undefined })
      return
    }
    this.setState({ maturity_days_min: Number(value) })
  }

  updateMaturityMax(event: React.ChangeEvent<HTMLInputElement>) {
    const { value } = event.target

    if (value === '' || value === undefined || value === null) {
      this.setState({ maturity_days_max: undefined })
      return
    }
    this.setState({ maturity_days_max: Number(value) })
  }

  updateNotes(event: React.ChangeEvent<HTMLTextAreaElement>) {
    const { value } = event.target

    this.setState({ notes: value })
  }

  add() {
    const data: PlantVarietyCreate = {
      plant: this.props.plantId,
      name: this.state.name,
      notes: this.state.notes
    }
    if (this.state.spacing !== undefined) {
      data.spacing = this.state.spacing
    }
    if (this.state.row_spacing !== undefined) {
      data.inter_row_spacing = this.state.row_spacing
    }
    if (this.state.per_square_foot !== undefined) {
      data.plants_per_square_foot = this.state.per_square_foot
    }
    if (this.state.germination_days_min !== undefined) {
      data.germination_days_min = this.state.germination_days_min
    }
    if (this.state.germination_days_max !== undefined) {
      data.germination_days_max = this.state.germination_days_max
    }
    if (this.state.maturity_days_min !== undefined) {
      data.maturity_days_min = this.state.maturity_days_min
    }
    if (this.state.maturity_days_max !== undefined) {
      data.maturity_days_max = this.state.maturity_days_max
    }
    addPlantVariety(data).done(this.props.done)
  }

  render() {
    return (
      <tr>
        <td>{this.props.familyName}</td>
        <td>{this.props.plantName}</td>
        <td>
          <input type="text" onChange={this.updateName} />
        </td>
        <td>
          <input type="number" onChange={this.updateSpacing}></input>
        </td>
        <td>
          <input type="number" onChange={this.updateRowSpacing}></input>
        </td>
        <td>
          <input type="number" onChange={this.updatePerSquareFtRate}></input>
        </td>
        <td>
          <input type="number" onChange={this.updateGerminationMin} /> - <input type="number" onChange={this.updateGerminationMax} />
        </td>
        <td>
          <input type="number" onChange={this.updateMaturityMin} /> - <input type="number" onChange={this.updateMaturityMax} />
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

interface PlantVarietyRowProps {
  variety: PlantVariety
  familyName: string
  plantName: string
}

class PlantVarietyRow extends React.Component<PlantVarietyRowProps> {
  render() {
    return (
      <tr>
        <td>{this.props.familyName}</td>
        <td>{this.props.plantName}</td>
        <td>{this.props.variety.name}</td>
        <td>{this.props.variety.spacing}</td>
        <td>{this.props.variety.inter_row_spacing}</td>
        <td>{this.props.variety.plants_per_square_foot}</td>
        <td>
          {this.props.variety.germination_days_min}-{this.props.variety.germination_days_max}
        </td>
        <td>
          {this.props.variety.maturity_days_min}-{this.props.variety.maturity_days_max}
        </td>
        <td>{this.props.variety.notes}</td>
      </tr>
    )
  }
}

interface PlantsViewState {
  showFamilyAdd: boolean
  showPlantAdd?: number
  showVarietyAdd?: number
  families: Array<PlantFamily>
  plants: Array<Plant>
  varieties: Array<PlantVariety>
}

class PlantsView extends React.Component<undefined, PlantsViewState> {
  timer?: number

  constructor(props: undefined) {
    super(props)

    this.state = {
      showFamilyAdd: false,
      showPlantAdd: undefined,
      showVarietyAdd: undefined,
      families: [],
      plants: [],
      varieties: []
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

  componentDidMount() {
    this.updateData()
    this.timer = setInterval(() => this.updateData(), 10000)
  }

  componentWillUnmount() {
    clearInterval(this.timer)
    this.timer = undefined
  }

  showNewFamilyAdd() {
    this.setState({
      showFamilyAdd: true
    })
  }

  hideNewFamilyAdd() {
    this.setState({
      showFamilyAdd: false
    })
  }

  showNewPlantAdd(familyId: number) {
    this.setState({
      showPlantAdd: familyId
    })
  }

  hideNewPlantAdd() {
    this.setState({
      showPlantAdd: undefined
    })
  }

  showNewVarietyAdd(plantId: number) {
    this.setState({
      showVarietyAdd: plantId
    })
  }

  hideNewVarietyAdd() {
    this.setState({
      showVarietyAdd: undefined
    })
  }

  updatePlantFamilyResponse(data: Array<PlantFamily>) {
    this.setState({
      families: data
    })
  }

  updatePlantResponse(data: Array<Plant>) {
    this.setState({
      plants: data
    })
  }

  updatePlantVarietiesResponse(data: Array<PlantVariety>) {
    this.setState({
      varieties: data
    })
  }

  async updateData() {
    this.updatePlantFamilyResponse(await getPlantFamilies())
    this.updatePlantVarietiesResponse(await getPlantVarieties())
    this.updatePlantResponse(await getPlants())
  }

  render() {
    const rows = []
    if (this.state.showFamilyAdd) {
      rows.push(<NewPlantFamilyRow done={this.hideNewFamilyAdd} key="family-add" />)
    }
    for (const f in this.state.families) {
      const familyData = this.state.families[f]
      rows.push(<PlantFamilyRow family={familyData} key={'family-' + familyData.pk} addNewPlant={this.showNewPlantAdd} />)
      if (this.state.showPlantAdd === familyData.pk) {
        rows.push(<NewPlantRow done={this.hideNewPlantAdd} familyId={familyData.pk} familyName={familyData.name} key="plant-add" />)
      }
      const plants = this.state.plants.filter((plant) => plant.family === familyData.pk)
      for (const p in plants) {
        const plantData = plants[p]
        rows.push(<PlantRow familyName={familyData.name} plant={plantData} key={'plant-' + plantData.pk} addNewPlantVariety={this.showNewVarietyAdd} />)
        if (this.state.showVarietyAdd === plantData.pk) {
          rows.push(<NewPlantVarietyRow done={this.hideNewVarietyAdd} plantId={plantData.pk} familyName={familyData.name} plantName={plantData.name} key="variety-add" />)
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
            <td>
              Family{' '}
              <a href="#" onClick={this.showNewFamilyAdd}>
                +
              </a>
            </td>
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
        <tbody>{rows}</tbody>
      </Table>
    )
  }
}

export { PlantsView }
