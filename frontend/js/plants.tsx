import 'bootstrap'
import 'bootstrap/dist/css/bootstrap.css'

import React from 'react'
import { Table, Button } from 'react-bootstrap'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { Plant, PlantCreate, PlantFamily, PlantFamilyCreate, PlantVariety, PlantVarietyCreate } from './types/plants'
import { addPlant, addPlantFamily, addPlantVariety, getPlantFamilies, getPlants, getPlantVarieties } from './api/plants'
import { queryKeys } from './query'

interface NewPlantFamilyRowProps {
  done: () => void
  createFamily: (data: PlantFamilyCreate) => Promise<void>
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

  async add() {
    await this.props.createFamily({
      name: this.state.name,
      notes: this.state.notes
    })
    this.props.done()
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
          <Button variant="link" className="p-0 align-baseline" aria-label="Add plant" onClick={this.addNewPlant}>
            +
          </Button>
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
  createPlant: (data: PlantCreate) => Promise<void>
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

  async add() {
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
    await this.props.createPlant(data)
    this.props.done()
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
          <Button variant="link" className="p-0 align-baseline" aria-label="Add variety" onClick={this.addNewPlantVariety}>
            +
          </Button>
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
  createVariety: (data: PlantVarietyCreate) => Promise<void>
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

  async add() {
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
    await this.props.createVariety(data)
    this.props.done()
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

function PlantsView() {
  const queryClient = useQueryClient()
  const [showFamilyAdd, setShowFamilyAdd] = React.useState(false)
  const [showPlantAdd, setShowPlantAdd] = React.useState<number>()
  const [showVarietyAdd, setShowVarietyAdd] = React.useState<number>()
  const { data: families = [] } = useQuery({
    queryKey: queryKeys.plants.families,
    queryFn: ({ signal }) => getPlantFamilies(signal)
  })
  const { data: plants = [] } = useQuery({
    queryKey: queryKeys.plants.plants,
    queryFn: ({ signal }) => getPlants(signal)
  })
  const { data: varieties = [] } = useQuery({
    queryKey: queryKeys.plants.varieties,
    queryFn: ({ signal }) => getPlantVarieties(signal)
  })
  const familyMutation = useMutation({
    mutationFn: addPlantFamily,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.plants.families })
  })
  const plantMutation = useMutation({
    mutationFn: addPlant,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.plants.plants })
  })
  const varietyMutation = useMutation({
    mutationFn: addPlantVariety,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.plants.varieties })
  })

  async function createFamily(data: PlantFamilyCreate) {
    await familyMutation.mutateAsync(data)
  }

  async function createPlant(data: PlantCreate) {
    await plantMutation.mutateAsync(data)
  }

  async function createVariety(data: PlantVarietyCreate) {
    await varietyMutation.mutateAsync(data)
  }

  const rows = []
  if (showFamilyAdd) {
    rows.push(<NewPlantFamilyRow createFamily={createFamily} done={() => setShowFamilyAdd(false)} key="family-add" />)
  }
  for (const familyData of families) {
    rows.push(<PlantFamilyRow family={familyData} key={'family-' + familyData.pk} addNewPlant={setShowPlantAdd} />)
    if (showPlantAdd === familyData.pk) {
      rows.push(<NewPlantRow createPlant={createPlant} done={() => setShowPlantAdd(undefined)} familyId={familyData.pk} familyName={familyData.name} key="plant-add" />)
    }
    const familyPlants = plants.filter((plant) => plant.family === familyData.pk)
    for (const plantData of familyPlants) {
      rows.push(<PlantRow familyName={familyData.name} plant={plantData} key={'plant-' + plantData.pk} addNewPlantVariety={setShowVarietyAdd} />)
      if (showVarietyAdd === plantData.pk) {
        rows.push(
          <NewPlantVarietyRow
            createVariety={createVariety}
            done={() => setShowVarietyAdd(undefined)}
            plantId={plantData.pk}
            familyName={familyData.name}
            plantName={plantData.name}
            key="variety-add"
          />
        )
      }
      const plantVarieties = varieties.filter((variety) => variety.plant === plantData.pk)
      for (const varietyData of plantVarieties) {
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
            <Button variant="link" className="p-0 align-baseline" aria-label="Add family" onClick={() => setShowFamilyAdd(true)}>
              +
            </Button>
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

export { PlantsView }
