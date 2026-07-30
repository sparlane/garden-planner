import 'bootstrap'
import 'bootstrap/dist/css/bootstrap.css'

import React from 'react'
import { Table, Button } from 'react-bootstrap'
import Select from 'react-select'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { Supplier, SupplierCreate } from './types/suppliers'
import { Seed, SeedCreate, SeedPacketCreate, SeedPacketDetails } from './types/seeds'
import { PlantVariety } from './types/plants'
import { SelectOption } from './types/others'
import { getPlantVarieties } from './api/plants'
import { addSeed, addSeedPacket, emptySeedPacket, getSeedPacketsCurrent, getSeeds } from './api/seeds'
import { addSupplier, getSuppliers } from './api/supplies'
import { queryKeys } from './query'

interface NewSeedSupplierRowProps {
  done: () => void
  createSupplier: (data: SupplierCreate) => Promise<void>
}

interface NewSeedSupplierRowState {
  name: string
  website?: string
  notes?: string
}

class NewSeedSupplierRow extends React.Component<NewSeedSupplierRowProps, NewSeedSupplierRowState> {
  constructor(props: NewSeedSupplierRowProps) {
    super(props)

    this.state = {
      name: '',
      website: undefined,
      notes: undefined
    }

    this.updateName = this.updateName.bind(this)
    this.updateWebsite = this.updateWebsite.bind(this)
    this.updateNotes = this.updateNotes.bind(this)

    this.add = this.add.bind(this)
  }

  updateName(event: React.ChangeEvent<HTMLInputElement>) {
    const { value } = event.target

    this.setState({ name: value })
  }

  updateWebsite(event: React.ChangeEvent<HTMLInputElement>) {
    const { value } = event.target

    this.setState({ website: value })
  }

  updateNotes(event: React.ChangeEvent<HTMLTextAreaElement>) {
    const { value } = event.target

    this.setState({ notes: value })
  }

  async add() {
    const data: SupplierCreate = {
      name: this.state.name,
      notes: this.state.notes
    }
    if (this.state.website && this.state.website !== '') {
      data.website = this.state.website
    }
    await this.props.createSupplier(data)
    this.props.done()
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

interface SeedSupplierRowProps {
  supplier: Supplier
}

class SeedSupplierRow extends React.Component<SeedSupplierRowProps> {
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

function SeedSuppliersTable() {
  const queryClient = useQueryClient()
  const [showSupplierAdd, setShowSupplierAdd] = React.useState(false)
  const { data: suppliers = [] } = useQuery({
    queryKey: queryKeys.suppliers.all,
    queryFn: ({ signal }) => getSuppliers(signal)
  })
  const supplierMutation = useMutation({
    mutationFn: addSupplier,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.suppliers.all })
  })

  async function createSupplier(data: SupplierCreate) {
    await supplierMutation.mutateAsync(data)
  }

  const rows = []
  if (showSupplierAdd) {
    rows.push(<NewSeedSupplierRow key="new" createSupplier={createSupplier} done={() => setShowSupplierAdd(false)} />)
  }
  for (const supplier of suppliers) {
    rows.push(<SeedSupplierRow key={supplier.pk} supplier={supplier} />)
  }
  return (
    <Table>
      <thead>
        <tr>
          <td>
            Name{' '}
            <a href="#" onClick={() => setShowSupplierAdd(true)}>
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

interface NewSeedRowProps {
  suppliers: Array<Supplier>
  varieties: Array<PlantVariety>
  done: () => void
  createSeed: (data: SeedCreate) => Promise<void>
}

interface NewSeedRowState {
  supplier?: number
  variety?: number
  supplierCode?: string
  website?: string
  notes?: string
}

class NewSeedRow extends React.Component<NewSeedRowProps, NewSeedRowState> {
  constructor(props: NewSeedRowProps) {
    super(props)

    this.state = {
      supplier: undefined,
      variety: undefined,
      supplierCode: undefined,
      website: undefined,
      notes: undefined
    }

    this.updateSupplier = this.updateSupplier.bind(this)
    this.updateVariety = this.updateVariety.bind(this)
    this.updateSupplierCode = this.updateSupplierCode.bind(this)
    this.updateWebsite = this.updateWebsite.bind(this)
    this.updateNotes = this.updateNotes.bind(this)

    this.add = this.add.bind(this)
  }

  updateSupplier(selectedSupplier: SelectOption | null) {
    const value = selectedSupplier?.value

    if (value === undefined || value === null) {
      this.setState({ supplier: undefined })
      return
    }
    this.setState({ supplier: Number(value) })
  }

  updateVariety(selectedVariety: SelectOption | null) {
    const value = selectedVariety?.value

    if (value === undefined || value === null) {
      this.setState({ variety: undefined })
      return
    }
    this.setState({ variety: Number(value) })
  }

  updateSupplierCode(event: React.ChangeEvent<HTMLInputElement>) {
    const { value } = event.target

    this.setState({ supplierCode: value })
  }

  updateWebsite(event: React.ChangeEvent<HTMLInputElement>) {
    const { value } = event.target

    this.setState({ website: value })
  }

  updateNotes(event: React.ChangeEvent<HTMLTextAreaElement>) {
    const { value } = event.target

    this.setState({ notes: value })
  }

  async add() {
    let { supplier } = this.state
    if (!supplier) {
      supplier = this.props.suppliers[0].pk
    }
    let { variety } = this.state
    if (!variety) {
      variety = this.props.varieties[0].pk
    }
    const data: SeedCreate = {
      supplier: supplier,
      plant_variety: variety,
      notes: this.state.notes
    }
    if (this.state.supplierCode !== undefined && this.state.supplierCode !== '') {
      data.supplier_code = this.state.supplierCode
    }
    if (this.state.website !== undefined && this.state.website !== '') {
      data.url = this.state.website
    }
    await this.props.createSeed(data)
    this.props.done()
  }

  render() {
    const suppliers = []
    for (const s in this.props.suppliers) {
      const supplierData = this.props.suppliers[s]
      suppliers.push({ value: supplierData.pk, label: supplierData.name })
    }
    const varieties = []
    for (const v in this.props.varieties) {
      const varietyData = this.props.varieties[v]
      varieties.push({ value: varietyData.pk, label: varietyData.name })
    }
    return (
      <tr>
        <td>
          <Select onChange={this.updateSupplier} options={suppliers} value={suppliers.find((o) => o.value === this.state.supplier)} />
        </td>
        <td>
          <Select onChange={this.updateVariety} options={varieties} value={varieties.find((o) => o.value === this.state.variety)} />
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

interface SeedRowProps {
  suppliers: Array<Supplier>
  varieties: Array<PlantVariety>
  seed: Seed
}

class SeedRow extends React.Component<SeedRowProps> {
  render() {
    const supplier = this.props.suppliers.find((s) => s.pk == this.props.seed.supplier)
    const variety = this.props.varieties.find((v) => v.pk === this.props.seed.plant_variety)
    return (
      <tr>
        <td>{supplier?.name}</td>
        <td>{variety?.name}</td>
        <td>{this.props.seed.supplier_code}</td>
        <td>
          <a href={this.props.seed.url}>{this.props.seed.url}</a>
        </td>
        <td>{this.props.seed.notes}</td>
      </tr>
    )
  }
}

function SeedTable() {
  const queryClient = useQueryClient()
  const [showSeedAdd, setShowSeedAdd] = React.useState(false)
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
  const seedMutation = useMutation({
    mutationFn: addSeed,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.seeds.catalog })
  })

  async function createSeed(data: SeedCreate) {
    await seedMutation.mutateAsync(data)
  }

  const rows = []
  if (showSeedAdd) {
    rows.push(<NewSeedRow key="new" suppliers={suppliers} varieties={varieties} createSeed={createSeed} done={() => setShowSeedAdd(false)} />)
  }
  for (const seed of seeds) {
    rows.push(<SeedRow key={seed.pk} suppliers={suppliers} varieties={varieties} seed={seed} />)
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
            <a href="#" onClick={() => setShowSeedAdd(true)}>
              +
            </a>
          </td>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </Table>
  )
}

interface NewSeedPacketRowProps {
  suppliers: Array<Supplier>
  varieties: Array<PlantVariety>
  seeds: Array<Seed>
  done: () => void
  createPacket: (data: SeedPacketCreate) => Promise<void>
}

interface NewSeedPacketRowState {
  seeds?: number
  purchaseDate?: string
  sowBy?: string
  notes?: string
}

class NewSeedPacketRow extends React.Component<NewSeedPacketRowProps, NewSeedPacketRowState> {
  constructor(props: NewSeedPacketRowProps) {
    super(props)

    this.state = {
      seeds: undefined,
      purchaseDate: undefined,
      sowBy: undefined,
      notes: undefined
    }

    this.updateSeeds = this.updateSeeds.bind(this)
    this.updatePurchaseDate = this.updatePurchaseDate.bind(this)
    this.updateSowBy = this.updateSowBy.bind(this)
    this.updateNotes = this.updateNotes.bind(this)

    this.add = this.add.bind(this)
  }

  updateSeeds(selectedSeeds: SelectOption | null) {
    const value = selectedSeeds?.value

    if (value === undefined || value === null) {
      this.setState({ seeds: undefined })
      return
    }

    this.setState({ seeds: Number(value) })
  }

  updatePurchaseDate(event: React.ChangeEvent<HTMLInputElement>) {
    const { value } = event.target

    this.setState({ purchaseDate: value })
  }

  updateSowBy(event: React.ChangeEvent<HTMLInputElement>) {
    const { value } = event.target

    this.setState({ sowBy: value })
  }

  updateNotes(event: React.ChangeEvent<HTMLTextAreaElement>) {
    const { value } = event.target

    this.setState({ notes: value })
  }

  async add() {
    let { seeds } = this.state
    if (!seeds) {
      seeds = this.props.seeds[0].pk
    }
    const data: SeedPacketCreate = {
      seeds: seeds,
      notes: this.state.notes
    }
    if (this.state.purchaseDate !== undefined && this.state.purchaseDate !== '') {
      data.purchase_date = this.state.purchaseDate
    }
    if (this.state.sowBy !== undefined && this.state.sowBy !== '') {
      data.sow_by = this.state.sowBy
    }
    await this.props.createPacket(data)
    this.props.done()
  }

  render() {
    const seeds = []
    for (const s in this.props.seeds) {
      const seedsData = this.props.seeds[s]
      const supplier = this.props.suppliers.find((s) => s.pk === seedsData.supplier)
      const variety = this.props.varieties.find((v) => v.pk === seedsData.plant_variety)
      seeds.push({ value: seedsData.pk, label: `${variety?.name} from ${supplier?.name}` })
    }
    return (
      <tr>
        <td>
          <Select onChange={this.updateSeeds} options={seeds} value={seeds.find((o) => o.value === this.state.seeds)} />
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

interface SeedPacketRowProps {
  seedPacket: SeedPacketDetails
  emptyPacket: (packetPk: number) => Promise<void>
}

class SeedPacketRow extends React.Component<SeedPacketRowProps> {
  constructor(props: SeedPacketRowProps) {
    super(props)

    this.empty = this.empty.bind(this)
  }

  async empty() {
    await this.props.emptyPacket(this.props.seedPacket.pk)
  }

  render() {
    return (
      <tr>
        <td>
          {this.props.seedPacket.plant}, {this.props.seedPacket.variety} from {this.props.seedPacket.supplier}
        </td>
        <td>{this.props.seedPacket.purchase_date}</td>
        <td>{this.props.seedPacket.sow_by}</td>
        <td>{this.props.seedPacket.seeds_planted_direct}</td>
        <td>
          {this.props.seedPacket.transplanted_count}/{this.props.seedPacket.seeds_planted_trays}
        </td>
        <td>{this.props.seedPacket.notes}</td>
        <td>
          <Button onClick={this.empty}>Empty</Button>
        </td>
      </tr>
    )
  }
}

function SeedStockTable() {
  const queryClient = useQueryClient()
  const [showSeedPacketAdd, setShowSeedPacketAdd] = React.useState(false)
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
    queryKey: queryKeys.seeds.packets.current,
    queryFn: ({ signal }) => getSeedPacketsCurrent(signal)
  })
  const packetMutation = useMutation({
    mutationFn: addSeedPacket,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.seeds.packets.all })
  })
  const emptyPacketMutation = useMutation({
    mutationFn: emptySeedPacket,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.seeds.packets.all })
  })

  async function createPacket(data: SeedPacketCreate) {
    await packetMutation.mutateAsync(data)
  }

  async function emptyPacket(packetPk: number) {
    await emptyPacketMutation.mutateAsync(packetPk)
  }

  const rows = []
  if (showSeedPacketAdd) {
    rows.push(<NewSeedPacketRow key="new" suppliers={suppliers} varieties={varieties} seeds={seeds} createPacket={createPacket} done={() => setShowSeedPacketAdd(false)} />)
  }
  for (const seedPacket of seedPackets) {
    rows.push(<SeedPacketRow key={seedPacket.pk} seedPacket={seedPacket} emptyPacket={emptyPacket} />)
  }
  return (
    <Table>
      <thead>
        <tr>
          <td>Seeds</td>
          <td>Purchase Date</td>
          <td>Sow By</td>
          <td>Direct Planted</td>
          <td>Transplanted/Seed Tray</td>
          <td>Notes</td>
          <td>
            <a href="#" onClick={() => setShowSeedPacketAdd(true)}>
              +
            </a>
          </td>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </Table>
  )
}

export { SeedSuppliersTable, SeedTable, SeedStockTable }
