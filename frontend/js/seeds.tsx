import 'bootstrap'
import 'bootstrap/dist/css/bootstrap.css'

import React from 'react'
import { Alert, Badge, Button, Form, Table } from 'react-bootstrap'
import Select from 'react-select'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { Supplier, SupplierCreate } from './types/suppliers'
import { InputTaxSource, PurchaseTaxTreatment, ReceiptDocumentType } from './types/inventory'
import { Seed, SeedCreate, SeedPacket, SeedPacketProvenance, SeedPacketReceiptCreate, SeedPacketReceiptDraft, SeedPacketReconciliation, SeedQuantityCertainty } from './types/seeds'
import { Plant, PlantVariety } from './types/plants'
import { SelectOption } from './types/others'
import { Workspace } from './types/workspace'
import { isAdvanced } from './workspace_mode'
import { getPlants, getPlantVarieties } from './api/plants'
import {
  addSeed,
  cancelSeedPacketReceipt,
  createSeedPacketReceipt,
  getAllSeedPackets,
  getSeedPacketReceipts,
  getSeeds,
  postSeedPacketReceipt,
  reconcileSeedPacket,
  updateSeedPacketReceipt
} from './api/seeds'
import { addSupplier, getSuppliers } from './api/supplies'
import { ReceiptSettlement } from './inventory/settlement'
import { queryKeys } from './query'
import { ApiError, formatQuantity } from './utils'

interface NewSeedSupplierRowProps {
  done: () => void
  createSupplier: (data: SupplierCreate) => Promise<void>
}

interface NewSeedSupplierRowState {
  name: string
  address: string
  gstStatus: 'registered' | 'unregistered' | 'unknown'
  gstNumber: string
  website?: string
  notes?: string
}

class NewSeedSupplierRow extends React.Component<NewSeedSupplierRowProps, NewSeedSupplierRowState> {
  constructor(props: NewSeedSupplierRowProps) {
    super(props)

    this.state = {
      name: '',
      address: '',
      gstStatus: 'unknown',
      gstNumber: '',
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
      address: this.state.address,
      gst_status: this.state.gstStatus,
      gst_number: this.state.gstStatus === 'registered' ? this.state.gstNumber : '',
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
          <textarea placeholder="Address" onChange={(event) => this.setState({ address: event.target.value })} />
          <select value={this.state.gstStatus} onChange={(event) => this.setState({ gstStatus: event.target.value as NewSeedSupplierRowState['gstStatus'] })}>
            <option value="unknown">GST status unknown</option>
            <option value="registered">GST registered</option>
            <option value="unregistered">Not GST registered</option>
          </select>
          {this.state.gstStatus === 'registered' && <input type="text" placeholder="GST number" onChange={(event) => this.setState({ gstNumber: event.target.value })} />}
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
        <td>
          {this.props.supplier.name}
          {this.props.supplier.is_system_default && (
            <Badge className="ms-2" bg="secondary">
              System default
            </Badge>
          )}
        </td>
        <td>
          <a href={this.props.supplier.website}>{this.props.supplier.website}</a>
        </td>
        <td>
          <div>{this.props.supplier.address || '—'}</div>
          <div>{this.props.supplier.gst_status === 'registered' ? `GST ${this.props.supplier.gst_number}` : this.props.supplier.gst_status}</div>
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
            <Button variant="link" className="p-0 align-baseline" aria-label="Add supplier" onClick={() => setShowSupplierAdd(true)}>
              +
            </Button>
          </td>
          <td>Website</td>
          <td>Tax identity</td>
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
    let { variety } = this.state
    if (!variety) {
      variety = this.props.varieties[0].pk
    }
    const data: SeedCreate = {
      plant_variety: variety,
      base_unit: this.state.baseUnit,
      notes: this.state.notes
    }
    // Left unset, the server fills in the workspace's system-default
    // supplier — the Basic Garden path for seed nobody bought from anyone.
    if (this.state.supplier !== undefined) {
      data.supplier = this.state.supplier
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
            <Button variant="link" className="p-0 align-baseline" aria-label="Add seed" onClick={() => setShowSeedAdd(true)}>
              +
            </Button>
          </td>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </Table>
  )
}

function seedLabel(seedPk: number, seeds: Array<Seed>, suppliers: Array<Supplier>, plants: Array<Plant>, varieties: Array<PlantVariety>): string {
  const seed = seeds.find((candidate) => candidate.pk === seedPk)
  const supplier = suppliers.find((candidate) => candidate.pk === seed?.supplier)
  const variety = varieties.find((candidate) => candidate.pk === seed?.plant_variety)
  const plant = plants.find((candidate) => candidate.pk === variety?.plant)
  return `${plant?.name ?? 'Unknown plant'} — ${variety?.name ?? 'Unknown variety'} from ${supplier?.name ?? 'unknown supplier'}`
}

interface PacketReceiptFormProps {
  seeds: Array<Seed>
  suppliers: Array<Supplier>
  plants: Array<Plant>
  varieties: Array<PlantVariety>
  draft?: SeedPacketReceiptDraft
  onSave: (data: SeedPacketReceiptCreate) => Promise<void>
  onCancel: () => void
  workspace: Workspace
}

function PacketReceiptForm({ seeds, suppliers, plants, varieties, draft, onSave, onCancel, workspace }: PacketReceiptFormProps) {
  const priceRequired = isAdvanced(workspace)
  const [seedPk, setSeedPk] = React.useState<number | undefined>(draft?.seeds)
  const [certainty, setCertainty] = React.useState<SeedQuantityCertainty>(draft?.quantity_certainty ?? 'unknown')
  const [quantity, setQuantity] = React.useState(draft?.quantity ? formatQuantity(draft.quantity) : '')
  const [price, setPrice] = React.useState(draft?.supplier_cost_incl_tax ?? draft?.line_price ?? (priceRequired ? '' : '0'))
  const [receivedDate, setReceivedDate] = React.useState(draft?.received_date ?? new Date().toISOString().slice(0, 10))
  const [sowBy, setSowBy] = React.useState(draft?.sow_by ?? '')
  const [supplierLot, setSupplierLot] = React.useState(draft?.supplier_lot_reference ?? '')
  const [vendorPk, setVendorPk] = React.useState<number | undefined>(draft?.supplier)
  const [supplierReference, setSupplierReference] = React.useState(draft?.supplier_reference ?? '')
  const [invoiceDate, setInvoiceDate] = React.useState(draft?.invoice_date ?? '')
  const [sourceDocumentType, setSourceDocumentType] = React.useState<ReceiptDocumentType>(draft?.source_document_type ?? 'none')
  const [sourceDocumentNumber, setSourceDocumentNumber] = React.useState(draft?.source_document_number ?? '')
  const [evidenceReference, setEvidenceReference] = React.useState(draft?.evidence_reference ?? '')
  const [evidenceUrl, setEvidenceUrl] = React.useState(draft?.evidence_url ?? '')
  const [taxRate, setTaxRate] = React.useState(draft?.tax_rate ?? '')
  const [taxTreatment, setTaxTreatment] = React.useState<PurchaseTaxTreatment>(draft?.tax_treatment ?? 'unknown')
  const [inputTaxSource, setInputTaxSource] = React.useState<InputTaxSource>(draft?.input_tax_source ?? 'none')
  const [inputTaxAmount, setInputTaxAmount] = React.useState(draft?.input_tax_amount ?? '0')
  const [claimInputTax, setClaimInputTax] = React.useState(draft?.claim_input_tax ?? false)
  const [claimablePercentage, setClaimablePercentage] = React.useState(draft?.claimable_percentage ?? '0')
  const [apportionmentBasis, setApportionmentBasis] = React.useState(draft?.apportionment_basis ?? '')
  const [notes, setNotes] = React.useState(draft?.notes ?? '')
  const [errors, setErrors] = React.useState<Record<string, string>>({})
  const editing = draft !== undefined

  function fieldError(field: string) {
    return errors[field] && <Form.Text className="text-danger">{errors[field]}</Form.Text>
  }

  function brandOf(pk: number | undefined) {
    return seeds.find((seed) => seed.pk === pk)?.supplier
  }

  function showServerErrors(caught: unknown) {
    if (!(caught instanceof ApiError) || typeof caught.body !== 'object' || caught.body === null) {
      setErrors({ non_field_errors: caught instanceof Error ? caught.message : String(caught) })
      return
    }
    setErrors(
      Object.fromEntries(
        Object.entries(caught.body as Record<string, unknown>).map(([field, value]) => {
          const message = Array.isArray(value) ? value[0] : value
          return [field, typeof message === 'string' ? message : String(message)]
        })
      )
    )
  }

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const selectedSeed = seedPk ?? seeds[0]?.pk
    if (!selectedSeed) return
    // An untouched vendor follows the brand rather than staying blank, so the
    // receipt records what the form was showing when it was submitted.
    const vendor = vendorPk ?? brandOf(selectedSeed)
    setErrors({})
    const data: SeedPacketReceiptCreate = {
      seeds: selectedSeed,
      quantity_certainty: certainty,
      line_price: price,
      received_date: receivedDate
    }
    data.quantity = certainty === 'unknown' ? null : quantity
    data.sow_by = sowBy || null
    data.supplier_lot_reference = supplierLot
    if (vendor) data.supplier = vendor
    data.supplier_reference = supplierReference
    data.invoice_date = invoiceDate || null
    data.source_document_type = sourceDocumentType
    data.source_document_number = sourceDocumentNumber
    data.evidence_reference = evidenceReference
    data.evidence_url = evidenceUrl
    data.supplier_cost_incl_tax = price
    data.tax_treatment = taxTreatment
    data.tax_rate = taxTreatment === 'standard' ? taxRate : '0'
    data.input_tax_source = inputTaxSource
    data.input_tax_amount = inputTaxSource === 'none' ? '0' : inputTaxAmount
    data.claim_input_tax = claimInputTax
    data.claimable_percentage = claimInputTax ? claimablePercentage : '0'
    data.apportionment_basis = apportionmentBasis
    data.notes = notes
    try {
      await onSave(data)
    } catch (caught) {
      showServerErrors(caught)
    }
  }

  return (
    <tr>
      <td colSpan={9}>
        <Form onSubmit={submit} className="row g-2 align-items-end">
          {Object.keys(errors).length > 0 && (
            <Alert variant="danger" className="mb-0">
              {Object.values(errors).join(' ')}
            </Alert>
          )}
          <Form.Group className="col-md-3">
            <Form.Label>Seed catalog</Form.Label>
            <Form.Select required disabled={editing} value={seedPk ?? ''} onChange={(event) => setSeedPk(Number(event.target.value))}>
              <option value="">Select…</option>
              {seeds.map((seed) => (
                <option key={seed.pk} value={seed.pk}>
                  {seedLabel(seed.pk, seeds, suppliers, plants, varieties)} ({seed.base_unit})
                </option>
              ))}
            </Form.Select>
          </Form.Group>
          <Form.Group className="col-md-3">
            <Form.Label>Bought from</Form.Label>
            <Form.Select value={vendorPk ?? brandOf(seedPk ?? seeds[0]?.pk) ?? ''} onChange={(event) => setVendorPk(event.target.value ? Number(event.target.value) : undefined)}>
              <option value="">Select…</option>
              {suppliers.map((supplier) => (
                <option key={supplier.pk} value={supplier.pk}>
                  {supplier.name}
                </option>
              ))}
            </Form.Select>
            <Form.Text className="text-muted">The shop or site that sold it. Defaults to the seed&apos;s brand.</Form.Text>
            {fieldError('supplier')}
          </Form.Group>
          <Form.Group className="col-md-2">
            <Form.Label>Quantity certainty</Form.Label>
            <Form.Select
              value={certainty}
              onChange={(event) => {
                const nextCertainty = event.target.value as SeedQuantityCertainty
                setCertainty(nextCertainty)
                if (nextCertainty === 'unknown') setQuantity('')
              }}
            >
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
            {fieldError('quantity')}
          </Form.Group>
          <Form.Group className="col-md-2">
            <Form.Label>Supplier cost incl tax</Form.Label>
            <Form.Control required={priceRequired} type="number" min="0" step="0.0001" value={price} onChange={(event) => setPrice(event.target.value)} />
            {fieldError('line_price')}
          </Form.Group>
          <Form.Group className="col-md-2">
            <Form.Label>Received</Form.Label>
            <Form.Control required type="date" value={receivedDate} onChange={(event) => setReceivedDate(event.target.value)} />
            {fieldError('received_date')}
          </Form.Group>
          <Form.Group className="col-md-2">
            <Form.Label>Sow by</Form.Label>
            <Form.Control type="date" value={sowBy} onChange={(event) => setSowBy(event.target.value)} />
            {fieldError('sow_by')}
          </Form.Group>
          <Form.Group className="col-md-3">
            <Form.Label>Supplier lot/reference</Form.Label>
            <Form.Control value={supplierLot} onChange={(event) => setSupplierLot(event.target.value)} />
            {fieldError('supplier_lot_reference')}
          </Form.Group>
          <Form.Group className="col-md-3">
            <Form.Label>Supplier reference</Form.Label>
            <Form.Control value={supplierReference} onChange={(event) => setSupplierReference(event.target.value)} />
            {fieldError('supplier_reference')}
          </Form.Group>
          <Form.Group className="col-md-2">
            <Form.Label>Invoice date</Form.Label>
            <Form.Control type="date" value={invoiceDate} onChange={(event) => setInvoiceDate(event.target.value)} />
          </Form.Group>
          <Form.Group className="col-md-2">
            <Form.Label>Tax treatment</Form.Label>
            <Form.Select value={taxTreatment} onChange={(event) => setTaxTreatment(event.target.value as PurchaseTaxTreatment)}>
              <option value="unknown">Unknown</option>
              <option value="standard">Standard-rated</option>
              <option value="zero_rated">Zero-rated</option>
              <option value="exempt">Exempt</option>
              <option value="out_of_scope">Out of scope</option>
            </Form.Select>
          </Form.Group>
          {taxTreatment === 'standard' && (
            <Form.Group className="col-md-2">
              <Form.Label>Tax rate %</Form.Label>
              <Form.Control type="number" min="0" step="0.0001" value={taxRate} onChange={(event) => setTaxRate(event.target.value)} />
            </Form.Group>
          )}
          <Form.Group className="col-md-2">
            <Form.Label>Input-tax source</Form.Label>
            <Form.Select value={inputTaxSource} onChange={(event) => setInputTaxSource(event.target.value as InputTaxSource)}>
              <option value="none">None</option>
              <option value="supplier">Supplier</option>
              <option value="customs">Customs</option>
              <option value="second_hand">Second-hand</option>
            </Form.Select>
          </Form.Group>
          {inputTaxSource !== 'none' && (
            <Form.Group className="col-md-2">
              <Form.Label>Input tax</Form.Label>
              <Form.Control type="number" min="0" step="0.0001" value={inputTaxAmount} onChange={(event) => setInputTaxAmount(event.target.value)} />
            </Form.Group>
          )}
          {inputTaxSource !== 'none' && (
            <Form.Group className="col-md-2">
              <Form.Check
                type="checkbox"
                label="Claim input tax"
                checked={claimInputTax}
                onChange={(event) => {
                  setClaimInputTax(event.target.checked)
                  setClaimablePercentage(event.target.checked ? '100' : '0')
                }}
              />
              {claimInputTax && (
                <Form.Control type="number" min="0" max="100" step="0.0001" value={claimablePercentage} onChange={(event) => setClaimablePercentage(event.target.value)} />
              )}
            </Form.Group>
          )}
          {claimInputTax && claimablePercentage !== '100' && (
            <Form.Group className="col-md-3">
              <Form.Label>Apportionment basis</Form.Label>
              <Form.Control value={apportionmentBasis} onChange={(event) => setApportionmentBasis(event.target.value)} />
            </Form.Group>
          )}
          <Form.Group className="col-md-3">
            <Form.Label>Source record</Form.Label>
            <Form.Select value={sourceDocumentType} onChange={(event) => setSourceDocumentType(event.target.value as ReceiptDocumentType)}>
              <option value="none">None recorded</option>
              <option value="taxable_supply">Taxable supply information</option>
              <option value="invoice">Invoice</option>
              <option value="receipt">Receipt</option>
              <option value="customs_entry">Customs entry</option>
              <option value="other">Other</option>
            </Form.Select>
          </Form.Group>
          <Form.Group className="col-md-3">
            <Form.Label>Document number</Form.Label>
            <Form.Control value={sourceDocumentNumber} onChange={(event) => setSourceDocumentNumber(event.target.value)} />
          </Form.Group>
          <Form.Group className="col-md-3">
            <Form.Label>Evidence reference</Form.Label>
            <Form.Control value={evidenceReference} onChange={(event) => setEvidenceReference(event.target.value)} />
          </Form.Group>
          <Form.Group className="col-md-3">
            <Form.Label>Evidence URL</Form.Label>
            <Form.Control type="url" value={evidenceUrl} onChange={(event) => setEvidenceUrl(event.target.value)} />
          </Form.Group>
          <Form.Group className="col-md-4">
            <Form.Label>Notes</Form.Label>
            <Form.Control value={notes} onChange={(event) => setNotes(event.target.value)} />
            {fieldError('notes')}
          </Form.Group>
          <div className="col-md-3">
            <Button type="submit">{editing ? 'Save changes' : 'Save draft'}</Button>{' '}
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
  onEdit: (draft: SeedPacketReceiptDraft) => void
  onPost: (pk: number) => Promise<void>
  onCancel: (pk: number) => Promise<void>
}

function ReceiptDraftRow({ draft, label, onEdit, onPost, onCancel }: ReceiptDraftRowProps) {
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
      <td colSpan={3}>Draft — confirm these normalized receipt details before posting.</td>
      <td>
        <Button size="sm" variant="outline-secondary" onClick={() => onEdit(draft)}>
          Edit
        </Button>{' '}
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

// The seeds column already names the brand, so this says the two things it
// cannot: who actually sold the packet, and which purchase record it belongs
// to. A packet with no receipt says so outright — it predates the receipt
// workflow or was never bought, and a blank would read as missing data.
function PacketProvenance({ provenance }: { provenance: SeedPacketProvenance }) {
  if (!provenance.receipt) {
    return (
      <span className="text-muted">
        No receipt
        {provenance.origin === 'opening' && (
          <>
            <br />
            <small>Opening balance</small>
          </>
        )}
      </span>
    )
  }
  return (
    <>
      {provenance.supplier_name}
      <br />
      <small className="text-muted">Receipt #{provenance.receipt}</small>
      <ReceiptSettlement receipt={provenance.receipt} settledOn={provenance.settled_on} />
    </>
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
      <td>
        <PacketProvenance provenance={packet.provenance} />
      </td>
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

type SeedPacketSortKey = 'seeds' | 'received' | 'boughtFrom' | 'sowBy' | 'receivedQuantity' | 'cost' | 'usage' | 'remaining'
type SortDirection = 'ascending' | 'descending'

interface SeedPacketTableRow {
  packet: SeedPacket
  label: string
}

function seedPacketSortValue(row: SeedPacketTableRow, key: SeedPacketSortKey): string | number | null {
  const { inventory } = row.packet
  switch (key) {
    case 'seeds':
      return row.label.toLocaleLowerCase()
    case 'received':
      return row.packet.purchase_date
    case 'boughtFrom':
      return row.packet.provenance.supplier_name?.toLocaleLowerCase() ?? null
    case 'sowBy':
      return row.packet.sow_by
    case 'receivedQuantity':
      return inventory?.received_quantity === null || inventory?.received_quantity === undefined ? null : Number(inventory.received_quantity)
    case 'cost':
      return inventory?.acquisition_total === null || inventory?.acquisition_total === undefined ? null : Number(inventory.acquisition_total)
    case 'usage':
      return inventory ? Number(inventory.sown_quantity) : null
    case 'remaining':
      return inventory?.remaining_quantity === null || inventory?.remaining_quantity === undefined ? null : Number(inventory.remaining_quantity)
  }
}

function compareSeedPacketRows(left: SeedPacketTableRow, right: SeedPacketTableRow, key: SeedPacketSortKey, direction: SortDirection): number {
  const leftValue = seedPacketSortValue(left, key)
  const rightValue = seedPacketSortValue(right, key)
  if (leftValue === null) return rightValue === null ? left.packet.pk - right.packet.pk : 1
  if (rightValue === null) return -1
  const comparison = typeof leftValue === 'number' && typeof rightValue === 'number' ? leftValue - rightValue : String(leftValue).localeCompare(String(rightValue))
  return (direction === 'ascending' ? comparison : -comparison) || left.packet.pk - right.packet.pk
}

interface SeedStockHeaderProps {
  label: string
  sortKey: SeedPacketSortKey
  activeSortKey: SeedPacketSortKey
  direction: SortDirection
  onSort: (key: SeedPacketSortKey) => void
  children?: React.ReactNode
}

function SeedStockHeader({ label, sortKey, activeSortKey, direction, onSort, children }: SeedStockHeaderProps) {
  const active = sortKey === activeSortKey
  return (
    <th aria-sort={active ? direction : 'none'}>
      <Button variant="link" className="p-0 fw-bold text-body text-decoration-none" onClick={() => onSort(sortKey)}>
        {label} {active ? (direction === 'ascending' ? '▲' : '▼') : '↕'}
      </Button>
      {children}
    </th>
  )
}

function SeedStockTable({ workspace }: { workspace: Workspace }) {
  const queryClient = useQueryClient()
  const [showReceipt, setShowReceipt] = React.useState(false)
  const [showEmptyPackets, setShowEmptyPackets] = React.useState(false)
  const [seedSearch, setSeedSearch] = React.useState('')
  const [sortKey, setSortKey] = React.useState<SeedPacketSortKey>('seeds')
  const [sortDirection, setSortDirection] = React.useState<SortDirection>('ascending')
  const [editingDraft, setEditingDraft] = React.useState<SeedPacketReceiptDraft | null>(null)
  const { data: suppliers = [] } = useQuery({ queryKey: queryKeys.suppliers.all, queryFn: ({ signal }) => getSuppliers(signal) })
  const { data: plants = [] } = useQuery({ queryKey: queryKeys.plants.plants, queryFn: ({ signal }) => getPlants(signal) })
  const { data: varieties = [] } = useQuery({ queryKey: queryKeys.plants.varieties, queryFn: ({ signal }) => getPlantVarieties(signal) })
  const { data: seeds = [] } = useQuery({ queryKey: queryKeys.seeds.catalog, queryFn: ({ signal }) => getSeeds(signal) })
  const { data: packets = [] } = useQuery({ queryKey: queryKeys.seeds.packets.raw, queryFn: ({ signal }) => getAllSeedPackets(signal) })
  const { data: drafts = [] } = useQuery({ queryKey: queryKeys.seeds.packetReceipts, queryFn: ({ signal }) => getSeedPacketReceipts(signal) })

  const invalidateStock = () =>
    Promise.all([queryClient.invalidateQueries({ queryKey: queryKeys.seeds.packets.all }), queryClient.invalidateQueries({ queryKey: queryKeys.seeds.packetReceipts })])
  const createMutation = useMutation({ mutationFn: createSeedPacketReceipt, onSuccess: invalidateStock })
  const updateMutation = useMutation({ mutationFn: ({ pk, data }: { pk: number; data: SeedPacketReceiptCreate }) => updateSeedPacketReceipt(pk, data), onSuccess: invalidateStock })
  const postMutation = useMutation({ mutationFn: postSeedPacketReceipt, onSuccess: invalidateStock })
  const cancelMutation = useMutation({ mutationFn: cancelSeedPacketReceipt, onSuccess: invalidateStock })
  const reconcileMutation = useMutation({
    mutationFn: ({ pk, data }: { pk: number; data: SeedPacketReconciliation }) => reconcileSeedPacket(pk, data),
    onSuccess: invalidateStock
  })
  const normalizedSearch = seedSearch.trim().toLocaleLowerCase()
  const packetRows = packets
    .map((packet) => ({ packet, label: seedLabel(packet.seeds, seeds, suppliers, plants, varieties) }))
    .filter(({ packet, label }) => {
      const remainingQuantity = packet.inventory?.remaining_quantity
      const visibleByStock = showEmptyPackets || (packet.empty !== true && (remainingQuantity === null || remainingQuantity === undefined || Number(remainingQuantity) !== 0))
      return visibleByStock && label.toLocaleLowerCase().includes(normalizedSearch)
    })
    .sort((left, right) => compareSeedPacketRows(left, right, sortKey, sortDirection))

  function changeSort(nextSortKey: SeedPacketSortKey) {
    if (nextSortKey === sortKey) {
      setSortDirection(sortDirection === 'ascending' ? 'descending' : 'ascending')
      return
    }
    setSortKey(nextSortKey)
    setSortDirection('ascending')
  }

  async function createReceipt(data: SeedPacketReceiptCreate) {
    await createMutation.mutateAsync(data)
    setShowReceipt(false)
  }

  async function saveDraft(data: SeedPacketReceiptCreate) {
    if (!editingDraft) return
    await updateMutation.mutateAsync({ pk: editingDraft.pk, data })
    setEditingDraft(null)
  }

  return (
    <Table responsive>
      <thead>
        <tr>
          <SeedStockHeader label="Seeds" sortKey="seeds" activeSortKey={sortKey} direction={sortDirection} onSort={changeSort}>
            <Form.Control
              className="mt-2"
              size="sm"
              type="search"
              aria-label="Search seeds"
              placeholder="Search seeds"
              value={seedSearch}
              onChange={(event) => setSeedSearch(event.target.value)}
            />
          </SeedStockHeader>
          <SeedStockHeader label="Received" sortKey="received" activeSortKey={sortKey} direction={sortDirection} onSort={changeSort} />
          <SeedStockHeader label="Bought from" sortKey="boughtFrom" activeSortKey={sortKey} direction={sortDirection} onSort={changeSort} />
          <SeedStockHeader label="Sow by" sortKey="sowBy" activeSortKey={sortKey} direction={sortDirection} onSort={changeSort} />
          <SeedStockHeader label="Received quantity" sortKey="receivedQuantity" activeSortKey={sortKey} direction={sortDirection} onSort={changeSort} />
          <SeedStockHeader label="Cost" sortKey="cost" activeSortKey={sortKey} direction={sortDirection} onSort={changeSort} />
          <SeedStockHeader label="Usage" sortKey="usage" activeSortKey={sortKey} direction={sortDirection} onSort={changeSort} />
          <SeedStockHeader label="Remaining / warnings" sortKey="remaining" activeSortKey={sortKey} direction={sortDirection} onSort={changeSort} />
          <th>
            <Button size="sm" onClick={() => setShowReceipt(true)}>
              Receive packet
            </Button>
            <Form.Check className="mt-2" type="checkbox" label="Show empty packets" checked={showEmptyPackets} onChange={(event) => setShowEmptyPackets(event.target.checked)} />
          </th>
        </tr>
      </thead>
      <tbody>
        {showReceipt && (
          <PacketReceiptForm
            seeds={seeds}
            suppliers={suppliers}
            plants={plants}
            varieties={varieties}
            onSave={createReceipt}
            onCancel={() => setShowReceipt(false)}
            workspace={workspace}
          />
        )}
        {drafts.map((draft) => (
          <ReceiptDraftRow
            key={`draft-${draft.pk}`}
            draft={draft}
            label={seedLabel(draft.seeds, seeds, suppliers, plants, varieties)}
            onEdit={setEditingDraft}
            onPost={(pk) => postMutation.mutateAsync(pk).then(() => undefined)}
            onCancel={(pk) => cancelMutation.mutateAsync(pk).then(() => undefined)}
          />
        ))}
        {editingDraft && (
          <PacketReceiptForm
            key={`edit-${editingDraft.pk}`}
            seeds={seeds}
            suppliers={suppliers}
            plants={plants}
            varieties={varieties}
            draft={editingDraft}
            onSave={saveDraft}
            onCancel={() => setEditingDraft(null)}
            workspace={workspace}
          />
        )}
        {packetRows.map(({ packet, label }) => (
          <SeedPacketRow key={packet.pk} packet={packet} label={label} onReconcile={(pk, data) => reconcileMutation.mutateAsync({ pk, data }).then(() => undefined)} />
        ))}
      </tbody>
    </Table>
  )
}

export { SeedSuppliersTable, SeedTable, SeedStockTable }
