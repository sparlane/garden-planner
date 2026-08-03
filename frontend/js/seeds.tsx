import 'bootstrap'
import 'bootstrap/dist/css/bootstrap.css'

import React from 'react'
import { Alert, Button, Form, Table } from 'react-bootstrap'
import Select from 'react-select'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { Supplier, SupplierCreate } from './types/suppliers'
import { Seed, SeedCreate, SeedPacket, SeedPacketReceiptCreate, SeedPacketReceiptDraft, SeedPacketReconciliation, SeedQuantityCertainty } from './types/seeds'
import { PlantVariety } from './types/plants'
import { SelectOption } from './types/others'
import { getPlantVarieties } from './api/plants'
import {
  addSeed,
  cancelSeedPacketReceipt,
  createSeedPacketReceipt,
  getAllSeedPackets,
  getSeedPacketReceipts,
  getSeeds,
  postSeedPacketReceipt,
  reconcileSeedPacket
} from './api/seeds'
import { addSupplier, getSuppliers } from './api/supplies'
import { queryKeys } from './query'
import { formatQuantity } from './utils'

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
  baseUnit: 'seed' | 'seed_cluster'
}

class NewSeedRow extends React.Component<NewSeedRowProps, NewSeedRowState> {
  constructor(props: NewSeedRowProps) {
    super(props)

    this.state = {
      supplier: undefined,
      variety: undefined,
      supplierCode: undefined,
      website: undefined,
      notes: undefined,
      baseUnit: 'seed'
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
      base_unit: this.state.baseUnit,
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
          <select value={this.state.baseUnit} onChange={(event) => this.setState({ baseUnit: event.target.value as 'seed' | 'seed_cluster' })}>
            <option value="seed">Individual seeds</option>
            <option value="seed_cluster">Multigerm seed clusters</option>
          </select>
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
        <td>{this.props.seed.base_unit === 'seed_cluster' ? 'Seed clusters' : 'Seeds'}</td>
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
          <td>Inventory unit</td>
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

function seedLabel(seedPk: number, seeds: Array<Seed>, suppliers: Array<Supplier>, varieties: Array<PlantVariety>): string {
  const seed = seeds.find((candidate) => candidate.pk === seedPk)
  const supplier = suppliers.find((candidate) => candidate.pk === seed?.supplier)
  const variety = varieties.find((candidate) => candidate.pk === seed?.plant_variety)
  return `${variety?.name ?? 'Unknown variety'} from ${supplier?.name ?? 'unknown supplier'}`
}

interface PacketReceiptFormProps {
  seeds: Array<Seed>
  suppliers: Array<Supplier>
  varieties: Array<PlantVariety>
  onCreate: (data: SeedPacketReceiptCreate) => Promise<void>
  onCancel: () => void
}

function PacketReceiptForm({ seeds, suppliers, varieties, onCreate, onCancel }: PacketReceiptFormProps) {
  const [seedPk, setSeedPk] = React.useState<number>()
  const [certainty, setCertainty] = React.useState<SeedQuantityCertainty>('unknown')
  const [quantity, setQuantity] = React.useState('')
  const [price, setPrice] = React.useState('')
  const [receivedDate, setReceivedDate] = React.useState(new Date().toISOString().slice(0, 10))
  const [sowBy, setSowBy] = React.useState('')
  const [supplierLot, setSupplierLot] = React.useState('')
  const [notes, setNotes] = React.useState('')

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const selectedSeed = seedPk ?? seeds[0]?.pk
    if (!selectedSeed) return
    const data: SeedPacketReceiptCreate = {
      seeds: selectedSeed,
      quantity_certainty: certainty,
      line_price: price,
      received_date: receivedDate
    }
    if (certainty !== 'unknown') data.quantity = quantity
    if (sowBy) data.sow_by = sowBy
    if (supplierLot) data.supplier_lot_reference = supplierLot
    if (notes) data.notes = notes
    await onCreate(data)
  }

  return (
    <tr>
      <td colSpan={8}>
        <Form onSubmit={submit} className="row g-2 align-items-end">
          <Form.Group className="col-md-3">
            <Form.Label>Seed catalog</Form.Label>
            <Form.Select required value={seedPk ?? ''} onChange={(event) => setSeedPk(Number(event.target.value))}>
              <option value="">Select…</option>
              {seeds.map((seed) => (
                <option key={seed.pk} value={seed.pk}>
                  {seedLabel(seed.pk, seeds, suppliers, varieties)} ({seed.base_unit})
                </option>
              ))}
            </Form.Select>
          </Form.Group>
          <Form.Group className="col-md-2">
            <Form.Label>Quantity certainty</Form.Label>
            <Form.Select value={certainty} onChange={(event) => setCertainty(event.target.value as SeedQuantityCertainty)}>
              <option value="unknown">Unknown</option>
              <option value="estimated">Estimated</option>
              <option value="exact">Exact</option>
            </Form.Select>
          </Form.Group>
          <Form.Group className="col-md-2">
            <Form.Label>Quantity</Form.Label>
            <Form.Control
              required={certainty !== 'unknown'}
              disabled={certainty === 'unknown'}
              type="number"
              min="0.000000001"
              step="0.000000001"
              value={quantity}
              onChange={(event) => setQuantity(event.target.value)}
            />
          </Form.Group>
          <Form.Group className="col-md-2">
            <Form.Label>Line price</Form.Label>
            <Form.Control required type="number" min="0" step="0.0001" value={price} onChange={(event) => setPrice(event.target.value)} />
          </Form.Group>
          <Form.Group className="col-md-2">
            <Form.Label>Received</Form.Label>
            <Form.Control required type="date" value={receivedDate} onChange={(event) => setReceivedDate(event.target.value)} />
          </Form.Group>
          <Form.Group className="col-md-2">
            <Form.Label>Sow by</Form.Label>
            <Form.Control type="date" value={sowBy} onChange={(event) => setSowBy(event.target.value)} />
          </Form.Group>
          <Form.Group className="col-md-3">
            <Form.Label>Supplier lot/reference</Form.Label>
            <Form.Control value={supplierLot} onChange={(event) => setSupplierLot(event.target.value)} />
          </Form.Group>
          <Form.Group className="col-md-4">
            <Form.Label>Notes</Form.Label>
            <Form.Control value={notes} onChange={(event) => setNotes(event.target.value)} />
          </Form.Group>
          <div className="col-md-3">
            <Button type="submit">Save draft</Button>{' '}
            <Button variant="secondary" onClick={onCancel}>
              Cancel
            </Button>
          </div>
        </Form>
      </td>
    </tr>
  )
}

interface ReceiptDraftRowProps {
  draft: SeedPacketReceiptDraft
  label: string
  onPost: (pk: number) => Promise<void>
  onCancel: (pk: number) => Promise<void>
}

function ReceiptDraftRow({ draft, label, onPost, onCancel }: ReceiptDraftRowProps) {
  if (draft.status !== 'draft') return null
  return (
    <tr className="table-warning">
      <td>{label}</td>
      <td>{draft.received_date}</td>
      <td>{draft.sow_by || '—'}</td>
      <td>
        {draft.quantity_certainty === 'unknown' ? 'Unknown' : `${formatQuantity(draft.quantity)} ${draft.base_unit}`} ({draft.quantity_certainty})
      </td>
      <td>{draft.line_price}</td>
      <td colSpan={2}>Draft — confirm these normalized receipt details before posting.</td>
      <td>
        <Button size="sm" onClick={() => onPost(draft.pk)}>
          Post receipt
        </Button>{' '}
        <Button size="sm" variant="outline-danger" onClick={() => onCancel(draft.pk)}>
          Cancel
        </Button>
      </td>
    </tr>
  )
}

interface SeedPacketRowProps {
  packet: SeedPacket
  label: string
  onReconcile: (pk: number, data: SeedPacketReconciliation) => Promise<void>
}

function SeedPacketRow({ packet, label, onReconcile }: SeedPacketRowProps) {
  const [count, setCount] = React.useState('')
  const [certainty, setCertainty] = React.useState<'exact' | 'estimated'>('exact')
  const [reason, setReason] = React.useState('')
  const inventory = packet.inventory

  async function reconcile() {
    if (!count || !reason) return
    await onReconcile(packet.pk, { counted_quantity: count, quantity_certainty: certainty, reason })
    setCount('')
    setReason('')
  }

  return (
    <tr>
      <td>{label}</td>
      <td>{packet.purchase_date || '—'}</td>
      <td>{packet.sow_by || '—'}</td>
      <td>
        {formatQuantity(inventory?.received_quantity, 'Unknown')} {inventory?.base_unit} ({inventory?.quantity_certainty ?? 'unlinked'})
      </td>
      <td>
        {inventory?.acquisition_total ?? 'Unknown'} {inventory?.currency_code}
        {inventory?.effective_base_unit_cost && ` (${inventory.effective_base_unit_cost}/${inventory.base_unit})`}
      </td>
      <td>
        {formatQuantity(inventory?.sown_quantity, '0')} sown; {formatQuantity(inventory?.adjustment_quantity, '0')} adjusted
      </td>
      <td>
        {formatQuantity(inventory?.remaining_quantity, 'Unknown')} {inventory?.base_unit}
        {inventory?.warnings.map((warning) => (
          <Alert key={warning} variant="warning" className="p-1 my-1">
            {warning}
          </Alert>
        ))}
        {inventory && <small>Lot #{inventory.lot}</small>}
      </td>
      <td>
        <Form.Control size="sm" type="number" min="0" step="0.000000001" placeholder="Physical count" value={count} onChange={(event) => setCount(event.target.value)} />
        <Form.Select size="sm" value={certainty} onChange={(event) => setCertainty(event.target.value as 'exact' | 'estimated')}>
          <option value="exact">Exact count</option>
          <option value="estimated">Estimated count</option>
        </Form.Select>
        <Form.Control size="sm" placeholder="Reason" value={reason} onChange={(event) => setReason(event.target.value)} />
        <Button size="sm" disabled={!count || !reason} onClick={reconcile}>
          Reconcile
        </Button>
      </td>
    </tr>
  )
}

function SeedStockTable() {
  const queryClient = useQueryClient()
  const [showReceipt, setShowReceipt] = React.useState(false)
  const { data: suppliers = [] } = useQuery({ queryKey: queryKeys.suppliers.all, queryFn: ({ signal }) => getSuppliers(signal) })
  const { data: varieties = [] } = useQuery({ queryKey: queryKeys.plants.varieties, queryFn: ({ signal }) => getPlantVarieties(signal) })
  const { data: seeds = [] } = useQuery({ queryKey: queryKeys.seeds.catalog, queryFn: ({ signal }) => getSeeds(signal) })
  const { data: packets = [] } = useQuery({ queryKey: queryKeys.seeds.packets.raw, queryFn: ({ signal }) => getAllSeedPackets(signal) })
  const { data: drafts = [] } = useQuery({ queryKey: queryKeys.seeds.packetReceipts, queryFn: ({ signal }) => getSeedPacketReceipts(signal) })

  const invalidateStock = () =>
    Promise.all([queryClient.invalidateQueries({ queryKey: queryKeys.seeds.packets.all }), queryClient.invalidateQueries({ queryKey: queryKeys.seeds.packetReceipts })])
  const createMutation = useMutation({ mutationFn: createSeedPacketReceipt, onSuccess: invalidateStock })
  const postMutation = useMutation({ mutationFn: postSeedPacketReceipt, onSuccess: invalidateStock })
  const cancelMutation = useMutation({ mutationFn: cancelSeedPacketReceipt, onSuccess: invalidateStock })
  const reconcileMutation = useMutation({
    mutationFn: ({ pk, data }: { pk: number; data: SeedPacketReconciliation }) => reconcileSeedPacket(pk, data),
    onSuccess: invalidateStock
  })

  async function createReceipt(data: SeedPacketReceiptCreate) {
    await createMutation.mutateAsync(data)
    setShowReceipt(false)
  }

  return (
    <Table responsive>
      <thead>
        <tr>
          <th>Seeds</th>
          <th>Received</th>
          <th>Sow by</th>
          <th>Received quantity</th>
          <th>Cost</th>
          <th>Usage</th>
          <th>Remaining / warnings</th>
          <th>
            <Button size="sm" onClick={() => setShowReceipt(true)}>
              Receive packet
            </Button>
          </th>
        </tr>
      </thead>
      <tbody>
        {showReceipt && <PacketReceiptForm seeds={seeds} suppliers={suppliers} varieties={varieties} onCreate={createReceipt} onCancel={() => setShowReceipt(false)} />}
        {drafts.map((draft) => (
          <ReceiptDraftRow
            key={`draft-${draft.pk}`}
            draft={draft}
            label={seedLabel(draft.seeds, seeds, suppliers, varieties)}
            onPost={(pk) => postMutation.mutateAsync(pk).then(() => undefined)}
            onCancel={(pk) => cancelMutation.mutateAsync(pk).then(() => undefined)}
          />
        ))}
        {packets.map((packet) => (
          <SeedPacketRow
            key={packet.pk}
            packet={packet}
            label={seedLabel(packet.seeds, seeds, suppliers, varieties)}
            onReconcile={(pk, data) => reconcileMutation.mutateAsync({ pk, data }).then(() => undefined)}
          />
        ))}
      </tbody>
    </Table>
  )
}

export { SeedSuppliersTable, SeedTable, SeedStockTable }
