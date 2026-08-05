import React from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Card, Col, Form, Row, Table } from 'react-bootstrap'

import {
  createInventoryItem,
  createItemUnitConversion,
  getInventoryItems,
  getInventoryUnits,
  getItemUnitConversions,
  setInventoryItemActive,
  setItemUnitConversionActive
} from './api/inventory'
import { queryKeys } from './query'
import {
  InventoryCategory,
  InventoryItem,
  InventoryItemCreate,
  InventoryItemFilters,
  InventoryTrackingMode,
  InventoryUnit,
  InventoryUsageBasis,
  UnitCode,
  UnitDimension
} from './types/inventory'
import { formatQuantity } from './utils'

const CATEGORY_LABELS: Record<InventoryCategory, string> = {
  seed: 'Seed',
  growing_media: 'Growing media',
  fertilizer_treatment: 'Fertilizer or treatment',
  label: 'Label',
  packaging: 'Packaging',
  pot_container: 'Pot or container',
  tray: 'Tray',
  other: 'Other physical input'
}

const TRACKING_LABELS: Record<InventoryTrackingMode, string> = {
  lot: 'Lot controlled',
  serialized: 'Serialized'
}

const USAGE_LABELS: Record<InventoryUsageBasis, string> = {
  cell_volume: 'Tray cell volume',
  surface_area: 'Surface-area rate',
  per_unit: 'Per plant or item',
  fixed: 'Fixed quantity',
  manual: 'Manual'
}

const RATE_DIMENSIONS: Partial<Record<InventoryUsageBasis, UnitDimension>> = {
  surface_area: 'area',
  per_unit: 'count'
}

interface ItemFormState {
  name: string
  sku: string
  category: InventoryCategory
  description: string
  baseUnit: UnitCode
  trackingMode: InventoryTrackingMode
  usageBasis: InventoryUsageBasis
  usageRate: string
  usageRateUnit: UnitCode
  fixedQuantity: string
}

const INITIAL_ITEM_FORM: ItemFormState = {
  name: '',
  sku: '',
  category: 'growing_media',
  description: '',
  baseUnit: 'ml',
  trackingMode: 'lot',
  usageBasis: 'manual',
  usageRate: '',
  usageRateUnit: 'ml',
  fixedQuantity: ''
}

interface InventoryItemFormProps {
  units: Array<InventoryUnit>
  onCreated: (item: InventoryItem) => void
}

function InventoryItemForm({ units, onCreated }: InventoryItemFormProps) {
  const queryClient = useQueryClient()
  const [form, setForm] = React.useState<ItemFormState>(INITIAL_ITEM_FORM)
  const mutation = useMutation({
    mutationFn: createInventoryItem,
    onSuccess: (item) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.inventory.all })
      setForm(INITIAL_ITEM_FORM)
      onCreated(item)
    }
  })

  function update<Field extends keyof ItemFormState>(field: Field, value: ItemFormState[Field]) {
    setForm((current) => ({ ...current, [field]: value }))
  }

  function updateCategory(category: InventoryCategory) {
    if (category === 'tray') {
      setForm((current) => ({ ...current, category, baseUnit: 'each', trackingMode: 'serialized' }))
    } else if (category === 'seed') {
      setForm((current) => ({ ...current, category, baseUnit: 'seed', trackingMode: 'lot' }))
    } else {
      setForm((current) => ({ ...current, category, trackingMode: 'lot' }))
    }
  }

  function updateUsageBasis(usageBasis: InventoryUsageBasis) {
    const dimension = RATE_DIMENSIONS[usageBasis]
    const firstCompatibleUnit = units.find((unit) => unit.dimension === dimension)
    setForm((current) => ({
      ...current,
      usageBasis,
      baseUnit:
        usageBasis === 'cell_volume' && units.find((unit) => unit.code === current.baseUnit)?.dimension !== 'volume'
          ? (units.find((unit) => unit.dimension === 'volume')?.code ?? current.baseUnit)
          : current.baseUnit,
      usageRateUnit: firstCompatibleUnit?.code ?? current.usageRateUnit
    }))
  }

  function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const item: InventoryItemCreate = {
      name: form.name,
      category: form.category,
      base_unit: form.baseUnit,
      tracking_mode: form.trackingMode,
      default_usage_basis: form.usageBasis
    }
    if (form.sku) item.sku = form.sku
    if (form.description) item.description = form.description
    if (RATE_DIMENSIONS[form.usageBasis]) {
      item.default_usage_rate = form.usageRate
      item.usage_rate_unit = form.usageRateUnit
    } else if (form.usageBasis === 'fixed') {
      item.default_fixed_quantity = form.fixedQuantity
    }
    mutation.mutate(item)
  }

  const rateDimension = RATE_DIMENSIONS[form.usageBasis]
  const rateUnits = units.filter((unit) => unit.dimension === rateDimension)
  const baseUnits = form.usageBasis === 'cell_volume' ? units.filter((unit) => unit.dimension === 'volume') : units

  return (
    <Card className="mb-4">
      <Card.Body>
        <Card.Title>Add inventory item</Card.Title>
        <Form onSubmit={submit}>
          <Row>
            <Col md={6}>
              <Form.Group className="mb-3" controlId="inventory-item-name">
                <Form.Label>Name</Form.Label>
                <Form.Control required maxLength={255} value={form.name} onChange={(event) => update('name', event.target.value)} />
              </Form.Group>
            </Col>
            <Col md={6}>
              <Form.Group className="mb-3" controlId="inventory-item-sku">
                <Form.Label>SKU</Form.Label>
                <Form.Control maxLength={64} value={form.sku} onChange={(event) => update('sku', event.target.value)} />
              </Form.Group>
            </Col>
          </Row>
          <Row>
            <Col md={6} lg={3}>
              <Form.Group className="mb-3" controlId="inventory-item-category">
                <Form.Label>Category</Form.Label>
                <Form.Select value={form.category} onChange={(event) => updateCategory(event.target.value as InventoryCategory)}>
                  {Object.entries(CATEGORY_LABELS).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </Form.Select>
              </Form.Group>
            </Col>
            <Col md={6} lg={3}>
              <Form.Group className="mb-3" controlId="inventory-item-unit">
                <Form.Label>Base unit</Form.Label>
                <Form.Select value={form.baseUnit} onChange={(event) => update('baseUnit', event.target.value as UnitCode)}>
                  {baseUnits.map((unit) => (
                    <option key={unit.code} value={unit.code}>
                      {unit.label} ({unit.code})
                    </option>
                  ))}
                </Form.Select>
              </Form.Group>
            </Col>
            <Col md={6} lg={3}>
              <Form.Group className="mb-3" controlId="inventory-item-tracking">
                <Form.Label>Tracking</Form.Label>
                <Form.Select value={form.trackingMode} onChange={(event) => update('trackingMode', event.target.value as InventoryTrackingMode)}>
                  {Object.entries(TRACKING_LABELS).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </Form.Select>
              </Form.Group>
            </Col>
            <Col md={6} lg={3}>
              <Form.Group className="mb-3" controlId="inventory-item-usage">
                <Form.Label>Default usage</Form.Label>
                <Form.Select value={form.usageBasis} onChange={(event) => updateUsageBasis(event.target.value as InventoryUsageBasis)}>
                  {Object.entries(USAGE_LABELS).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </Form.Select>
                {form.usageBasis === 'cell_volume' && <Form.Text muted>Usage comes from each selected tray&apos;s cell volume, including when tray cell sizes differ.</Form.Text>}
              </Form.Group>
            </Col>
          </Row>
          {rateDimension && (
            <Row>
              <Col md={6}>
                <Form.Group className="mb-3" controlId="inventory-item-rate">
                  <Form.Label>Base-unit quantity per rate unit</Form.Label>
                  <Form.Control required type="number" min="0.000000001" step="0.000000001" value={form.usageRate} onChange={(event) => update('usageRate', event.target.value)} />
                </Form.Group>
              </Col>
              <Col md={6}>
                <Form.Group className="mb-3" controlId="inventory-item-rate-unit">
                  <Form.Label>Rate unit</Form.Label>
                  <Form.Select value={form.usageRateUnit} onChange={(event) => update('usageRateUnit', event.target.value as UnitCode)}>
                    {rateUnits.map((unit) => (
                      <option key={unit.code} value={unit.code}>
                        {unit.label} ({unit.code})
                      </option>
                    ))}
                  </Form.Select>
                </Form.Group>
              </Col>
            </Row>
          )}
          {form.usageBasis === 'fixed' && (
            <Form.Group className="mb-3" controlId="inventory-item-fixed-quantity">
              <Form.Label>Default quantity in {form.baseUnit}</Form.Label>
              <Form.Control
                required
                type="number"
                min="0.000000001"
                step="0.000000001"
                value={form.fixedQuantity}
                onChange={(event) => update('fixedQuantity', event.target.value)}
              />
            </Form.Group>
          )}
          <Form.Group className="mb-3" controlId="inventory-item-description">
            <Form.Label>Description</Form.Label>
            <Form.Control as="textarea" value={form.description} onChange={(event) => update('description', event.target.value)} />
          </Form.Group>
          <Button type="submit" disabled={mutation.isPending}>
            {mutation.isPending ? 'Adding…' : 'Add item'}
          </Button>
        </Form>
      </Card.Body>
    </Card>
  )
}

interface ConversionPanelProps {
  item: InventoryItem
}

function ConversionPanel({ item }: ConversionPanelProps) {
  const queryClient = useQueryClient()
  const [label, setLabel] = React.useState('')
  const [multiplier, setMultiplier] = React.useState('')
  const { data: conversions = [] } = useQuery({
    queryKey: queryKeys.inventory.conversions(item.pk),
    queryFn: ({ signal }) => getItemUnitConversions(item.pk, signal)
  })
  const createMutation = useMutation({
    mutationFn: createItemUnitConversion,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.inventory.conversions(item.pk) })
      setLabel('')
      setMultiplier('')
    }
  })
  const activeMutation = useMutation({
    mutationFn: ({ pk, active }: { pk: number; active: boolean }) => setItemUnitConversionActive(pk, active),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.inventory.conversions(item.pk) })
  })

  function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    createMutation.mutate({ item: item.pk, label, multiplier })
  }

  return (
    <Card className="mt-4">
      <Card.Body>
        <Card.Title>Package and application units for {item.name}</Card.Title>
        <p className="text-muted">Each multiplier is stored as a quantity of {item.base_unit}.</p>
        <Table responsive striped>
          <thead>
            <tr>
              <th>Label</th>
              <th>Multiplier</th>
              <th>Status</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody>
            {conversions.map((conversion) => (
              <tr key={conversion.pk}>
                <td>{conversion.label}</td>
                <td>
                  {formatQuantity(conversion.multiplier)} {conversion.base_unit}
                </td>
                <td>{conversion.active ? 'Active' : 'Inactive'}</td>
                <td>
                  {conversion.active && (
                    <Button size="sm" variant="outline-danger" disabled={activeMutation.isPending} onClick={() => activeMutation.mutate({ pk: conversion.pk, active: false })}>
                      Deactivate
                    </Button>
                  )}
                </td>
              </tr>
            ))}
            {conversions.length === 0 && (
              <tr>
                <td colSpan={4}>No item-specific units configured.</td>
              </tr>
            )}
          </tbody>
        </Table>
        <Form onSubmit={submit}>
          <Row className="align-items-end">
            <Col md={5}>
              <Form.Group controlId="inventory-conversion-label">
                <Form.Label>Unit label</Form.Label>
                <Form.Control required maxLength={128} placeholder="40 L bag" value={label} onChange={(event) => setLabel(event.target.value)} />
              </Form.Group>
            </Col>
            <Col md={5}>
              <Form.Group controlId="inventory-conversion-multiplier">
                <Form.Label>Quantity in {item.base_unit}</Form.Label>
                <Form.Control
                  required
                  type="number"
                  min="0.000000001"
                  step="0.000000001"
                  placeholder="40000"
                  value={multiplier}
                  onChange={(event) => setMultiplier(event.target.value)}
                />
              </Form.Group>
            </Col>
            <Col md={2}>
              <Button className="mt-3" type="submit" disabled={createMutation.isPending}>
                {createMutation.isPending ? 'Adding…' : 'Add unit'}
              </Button>
            </Col>
          </Row>
        </Form>
      </Card.Body>
    </Card>
  )
}

function InventoryCatalog() {
  const queryClient = useQueryClient()
  const [search, setSearch] = React.useState('')
  const [category, setCategory] = React.useState<InventoryCategory | ''>('')
  const [trackingMode, setTrackingMode] = React.useState<InventoryTrackingMode | ''>('')
  const [status, setStatus] = React.useState<'all' | 'active' | 'inactive'>('active')
  const [selectedItemPk, setSelectedItemPk] = React.useState<number | null>(null)
  const filters: InventoryItemFilters = {
    search: search || undefined,
    category: category || undefined,
    tracking_mode: trackingMode || undefined,
    active: status === 'all' ? undefined : status === 'active'
  }
  const { data: units = [] } = useQuery({
    queryKey: queryKeys.inventory.units,
    queryFn: ({ signal }) => getInventoryUnits(signal)
  })
  const { data: items = [] } = useQuery({
    queryKey: queryKeys.inventory.items(search, category, trackingMode, status),
    queryFn: ({ signal }) => getInventoryItems(filters, signal)
  })
  const activeMutation = useMutation({
    mutationFn: ({ pk, active }: { pk: number; active: boolean }) => setInventoryItemActive(pk, active),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.inventory.all })
  })
  const selectedItem = items.find((item) => item.pk === selectedItemPk) ?? null

  return (
    <main className="container py-3">
      <h1>Inventory catalog</h1>
      <p>Define physical inputs in canonical units. Receipts and balances are added by the stock ledger.</p>
      {units.length > 0 && <InventoryItemForm units={units} onCreated={(item) => setSelectedItemPk(item.pk)} />}
      <Card>
        <Card.Body>
          <Card.Title>Items</Card.Title>
          <Row className="mb-3">
            <Col md={4}>
              <Form.Control aria-label="Search inventory" placeholder="Search name, SKU, or description" value={search} onChange={(event) => setSearch(event.target.value)} />
            </Col>
            <Col md={3}>
              <Form.Select aria-label="Filter category" value={category} onChange={(event) => setCategory(event.target.value as InventoryCategory | '')}>
                <option value="">All categories</option>
                {Object.entries(CATEGORY_LABELS).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </Form.Select>
            </Col>
            <Col md={3}>
              <Form.Select aria-label="Filter tracking" value={trackingMode} onChange={(event) => setTrackingMode(event.target.value as InventoryTrackingMode | '')}>
                <option value="">All tracking modes</option>
                {Object.entries(TRACKING_LABELS).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </Form.Select>
            </Col>
            <Col md={2}>
              <Form.Select aria-label="Filter status" value={status} onChange={(event) => setStatus(event.target.value as typeof status)}>
                <option value="active">Active</option>
                <option value="inactive">Inactive</option>
                <option value="all">All statuses</option>
              </Form.Select>
            </Col>
          </Row>
          <Table responsive hover striped>
            <thead>
              <tr>
                <th>Name</th>
                <th>SKU</th>
                <th>Category</th>
                <th>Base unit</th>
                <th>Tracking</th>
                <th>Usage</th>
                <th>Status</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.pk} className={selectedItemPk === item.pk ? 'table-primary' : undefined}>
                  <td>{item.name}</td>
                  <td>{item.sku}</td>
                  <td>{CATEGORY_LABELS[item.category]}</td>
                  <td>{item.base_unit}</td>
                  <td>{TRACKING_LABELS[item.tracking_mode]}</td>
                  <td>{USAGE_LABELS[item.default_usage_basis]}</td>
                  <td>{item.active ? 'Active' : 'Inactive'}</td>
                  <td className="text-nowrap">
                    <Button size="sm" variant="outline-primary" onClick={() => setSelectedItemPk(item.pk)}>
                      Units
                    </Button>{' '}
                    {item.active && (
                      <Button size="sm" variant="outline-danger" disabled={activeMutation.isPending} onClick={() => activeMutation.mutate({ pk: item.pk, active: false })}>
                        Deactivate
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
              {items.length === 0 && (
                <tr>
                  <td colSpan={8}>No inventory items match these filters.</td>
                </tr>
              )}
            </tbody>
          </Table>
        </Card.Body>
      </Card>
      {selectedItem && <ConversionPanel item={selectedItem} />}
    </main>
  )
}

export { InventoryCatalog }
