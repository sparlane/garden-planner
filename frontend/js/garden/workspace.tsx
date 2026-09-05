import React, { useMemo, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Alert, Badge, Button, ButtonGroup, Card, Col, Form, Modal, Row } from 'react-bootstrap'

import { previewGardenGeometry, updateGardenGeometry } from '../api/garden'
import { queryKeys } from '../query'
import { GardenArea, GardenBed, GardenGeometryEdit, GardenGeometryResource, GardenRow, GardenSquare } from '../types/garden'
import { GardenRegisterRow, GardenSquarePlanting } from '../types/plantings'
import { GardenCanvas } from './canvas'

type GardenLayer = 'occupancy' | 'crop' | 'health' | 'work' | 'season'
type GardenView = 'map' | 'list' | 'both'
type EditableGeometry = GardenArea | GardenBed | GardenRow | GardenSquare

interface GardenWorkspaceProps {
  area: GardenArea
  beds: Array<GardenBed>
  rows: Array<GardenRow>
  squares: Array<GardenSquare>
  plantings: Array<GardenSquarePlanting>
  registerRows: Array<GardenRegisterRow>
  onSelectBed: (pk: number) => void
  onSelectRow: (pk: number) => void
  onSelectSquare: (pk: number) => void
}

interface LayoutEditModalProps {
  resource: GardenGeometryResource
  geometry: EditableGeometry
  onClose: () => void
}

const LAYER_LABELS: Record<GardenLayer, string> = {
  occupancy: 'Occupancy',
  crop: 'Crop',
  health: 'Health',
  work: 'Due work',
  season: 'Season'
}

function rowsAt(registerRows: Array<GardenRegisterRow>, kind: 'row' | 'square', pk: number): Array<GardenRegisterRow> {
  return registerRows.filter((row) => row.location === `${kind}:${pk}`)
}

function cropNames(rows: Array<GardenRegisterRow>): string {
  return [...new Set(rows.map((row) => `${row.plant_name} — ${row.variety_name}`))].join(', ')
}

function squareDescription(square: GardenSquare, plantings: Array<GardenSquarePlanting>, records: Array<GardenRegisterRow>): string {
  if (plantings.length === 0 && records.length === 0) return `${square.name}: empty`
  const crops = cropNames(records) || [...new Set(plantings.map((planting) => `${planting.plant} — ${planting.variety}`))].join(', ')
  const quantity = records.reduce((total, row) => total + row.quantity, 0) || plantings.reduce((total, planting) => total + planting.quantity, 0)
  return `${square.name}: ${crops}, quantity ${quantity}`
}

function geometryPayload(geometry: EditableGeometry): GardenGeometryEdit {
  const payload: GardenGeometryEdit = {
    name: geometry.name,
    size_x: geometry.size_x,
    size_y: geometry.size_y
  }
  if ('placement_x' in geometry) {
    payload.placement_x = geometry.placement_x
    payload.placement_y = geometry.placement_y
  }
  return payload
}

function LayoutEditModal({ resource, geometry, onClose }: LayoutEditModalProps) {
  const queryClient = useQueryClient()
  const original = useMemo(() => geometryPayload(geometry), [geometry])
  const [draft, setDraft] = useState<GardenGeometryEdit>(original)
  const [warnings, setWarnings] = useState<Array<string>>()
  const preview = useMutation({
    mutationFn: () => previewGardenGeometry(resource, geometry.pk, draft),
    onSuccess: (result) => setWarnings(result.warnings)
  })
  const save = useMutation({
    mutationFn: () => updateGardenGeometry(resource, geometry.pk, draft),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.garden.all })
      onClose()
    }
  })

  function change(field: keyof GardenGeometryEdit, value: string) {
    setDraft((current) => ({ ...current, [field]: field === 'name' ? value : Number(value) }))
    setWarnings(undefined)
  }

  const title = resource === 'areas' ? 'area' : resource.slice(0, -1)
  return (
    <Modal show onHide={onClose} size="lg" aria-labelledby="layout-edit-title">
      <Modal.Header closeButton>
        <Modal.Title id="layout-edit-title">Edit {title} layout</Modal.Title>
      </Modal.Header>
      <Modal.Body>
        <Alert variant="info">Changes stay in this preview until you review them and choose Save. Current and historical records are never deleted by a geometry edit.</Alert>
        <Form>
          <Row className="g-2">
            <Col xs={12}>
              <Form.Label htmlFor="layout-name">Name</Form.Label>
              <Form.Control id="layout-name" value={draft.name ?? ''} onChange={(event) => change('name', event.target.value)} />
            </Col>
            {'placement_x' in draft && (
              <>
                <Col sm={3}>
                  <Form.Label htmlFor="layout-x">Position X</Form.Label>
                  <Form.Control id="layout-x" type="number" min={0} value={draft.placement_x} onChange={(event) => change('placement_x', event.target.value)} />
                </Col>
                <Col sm={3}>
                  <Form.Label htmlFor="layout-y">Position Y</Form.Label>
                  <Form.Control id="layout-y" type="number" min={0} value={draft.placement_y} onChange={(event) => change('placement_y', event.target.value)} />
                </Col>
              </>
            )}
            <Col sm={3}>
              <Form.Label htmlFor="layout-width">Width</Form.Label>
              <Form.Control id="layout-width" type="number" min={1} value={draft.size_x} onChange={(event) => change('size_x', event.target.value)} />
            </Col>
            <Col sm={3}>
              <Form.Label htmlFor="layout-height">Height</Form.Label>
              <Form.Control id="layout-height" type="number" min={1} value={draft.size_y} onChange={(event) => change('size_y', event.target.value)} />
            </Col>
          </Row>
        </Form>
        <Card className="mt-3" aria-live="polite">
          <Card.Header>Unsaved preview</Card.Header>
          <Card.Body>
            <strong>{draft.name}</strong> · {draft.size_x} × {draft.size_y}
            {'placement_x' in draft && ` at ${draft.placement_x}, ${draft.placement_y}`}
          </Card.Body>
        </Card>
        {warnings?.map((warning) => (
          <Alert key={warning} variant="warning" className="mt-3 mb-0">
            {warning}
          </Alert>
        ))}
        {warnings !== undefined && warnings.length === 0 && (
          <Alert variant="success" className="mt-3 mb-0">
            This change is valid and will not orphan any saved geometry.
          </Alert>
        )}
      </Modal.Body>
      <Modal.Footer>
        <Button
          variant="outline-secondary"
          onClick={() => {
            setDraft(original)
            setWarnings(undefined)
          }}
        >
          Undo changes
        </Button>
        <Button variant="outline-primary" disabled={preview.isPending} onClick={() => preview.mutate()}>
          {preview.isPending ? 'Reviewing…' : 'Review change'}
        </Button>
        <Button variant="primary" disabled={warnings === undefined || save.isPending} onClick={() => save.mutate()}>
          {save.isPending ? 'Saving…' : 'Save'}
        </Button>
      </Modal.Footer>
    </Modal>
  )
}

function GardenWorkspace({ area, beds, rows, squares, plantings, registerRows, onSelectBed, onSelectRow, onSelectSquare }: GardenWorkspaceProps) {
  const [layer, setLayer] = useState<GardenLayer>('occupancy')
  const [view, setView] = useState<GardenView>('both')
  const [search, setSearch] = useState('')
  const normalizedSearch = search.trim().toLocaleLowerCase()
  const plantingsBySquare = useMemo(() => {
    const grouped = new Map<number, Array<GardenSquarePlanting>>()
    for (const planting of plantings) {
      const values = grouped.get(planting.location.pk) ?? []
      values.push(planting)
      grouped.set(planting.location.pk, values)
    }
    return grouped
  }, [plantings])
  const registerBySquare = useMemo(() => new Map(squares.map((square) => [square.pk, rowsAt(registerRows, 'square', square.pk)])), [registerRows, squares])
  const registerByRow = useMemo(() => new Map(rows.map((row) => [row.pk, rowsAt(registerRows, 'row', row.pk)])), [registerRows, rows])

  const describeSquare = (square: GardenSquare) => squareDescription(square, plantingsBySquare.get(square.pk) ?? [], registerBySquare.get(square.pk) ?? [])
  const describeRow = (row: GardenRow) => {
    const records = registerByRow.get(row.pk) ?? []
    return records.length === 0 ? `${row.name}: empty` : `${row.name}: ${cropNames(records)}, quantity ${records.reduce((total, record) => total + record.quantity, 0)}`
  }
  const describeBed = (bed: GardenBed) => `${bed.name}: ${bed.kind.replace('_', ' ')}`
  const matches = (description: string) => normalizedSearch === '' || description.toLocaleLowerCase().includes(normalizedSearch)
  const squareClass = (square: GardenSquare) => {
    const records = registerBySquare.get(square.pk) ?? []
    const occupied = (plantingsBySquare.get(square.pk) ?? []).length > 0 || records.length > 0
    const classes = ['garden-square', occupied ? 'garden-square--planted' : 'garden-square--empty']
    if (!matches(describeSquare(square))) classes.push('garden-geometry--hidden')
    if (layer === 'crop' && records.length > 0) classes.push(`garden-crop-${records[0].plant % 6}`)
    if (layer === 'health' && records.some((record) => record.health_flag)) classes.push('garden-square--attention')
    if (layer === 'work' && records.some((record) => record.next_task !== null)) classes.push('garden-square--due')
    if (layer === 'season' && records.some((record) => record.expected_harvest_early !== null || record.expected_harvest_late !== null)) classes.push('garden-square--season')
    return classes.join(' ')
  }
  const rowClass = (row: GardenRow) => {
    const records = registerByRow.get(row.pk) ?? []
    const classes = ['garden-row', records.length > 0 ? 'garden-row--planted' : 'garden-row--empty']
    if (!matches(describeRow(row))) classes.push('garden-geometry--hidden')
    if (layer === 'crop' && records.length > 0) classes.push(`garden-crop-${records[0].plant % 6}`)
    if (layer === 'health' && records.some((record) => record.health_flag)) classes.push('garden-square--attention')
    if (layer === 'work' && records.some((record) => record.next_task !== null)) classes.push('garden-square--due')
    if (layer === 'season' && records.some((record) => record.expected_harvest_early !== null || record.expected_harvest_late !== null)) classes.push('garden-square--season')
    return classes.join(' ')
  }
  const bedClass = (bed: GardenBed) => {
    const hasMatchingChild =
      rows.some((row) => row.bed === bed.pk && matches(describeRow(row))) || squares.some((square) => square.bed === bed.pk && matches(describeSquare(square)))
    return `garden-bed garden-bed--${bed.kind}${matches(describeBed(bed)) || hasMatchingChild ? '' : ' garden-geometry--hidden'}`
  }
  const visibleSquares = squares.filter((square) => matches(describeSquare(square)))
  const visibleRows = rows.filter((row) => matches(describeRow(row)))

  return (
    <>
      <div className="garden-workspace-toolbar">
        <Form.Group className="garden-workspace-search">
          <Form.Label htmlFor="garden-search">Search crop, variety, bed, row, or square</Form.Label>
          <Form.Control id="garden-search" type="search" value={search} onChange={(event) => setSearch(event.target.value)} />
        </Form.Group>
        <div>
          <div className="form-label">Layer</div>
          <ButtonGroup className="flex-wrap" aria-label="Garden information layer">
            {(Object.entries(LAYER_LABELS) as Array<[GardenLayer, string]>).map(([value, label]) => (
              <Button key={value} variant={layer === value ? 'primary' : 'outline-primary'} aria-pressed={layer === value} onClick={() => setLayer(value)}>
                {label}
              </Button>
            ))}
          </ButtonGroup>
        </div>
        <div>
          <div className="form-label">View</div>
          <ButtonGroup aria-label="Garden workspace view">
            {(['map', 'list', 'both'] as Array<GardenView>).map((value) => (
              <Button key={value} variant={view === value ? 'secondary' : 'outline-secondary'} aria-pressed={view === value} onClick={() => setView(value)}>
                {value[0].toUpperCase() + value.slice(1)}
              </Button>
            ))}
          </ButtonGroup>
        </div>
      </div>
      <div className="garden-legend" aria-label={`${LAYER_LABELS[layer]} layer legend`}>
        <strong>{LAYER_LABELS[layer]}:</strong> {layer === 'occupancy' && 'green is planted; tan is empty'}
        {layer === 'crop' && 'colours distinguish crops'}
        {layer === 'health' && 'red marks an active health flag'}
        {layer === 'work' && 'blue marks due work'}
        {layer === 'season' && 'gold marks a crop with an expected harvest season'}
      </div>
      {normalizedSearch !== '' && (
        <p className="small text-muted" role="status">
          {visibleRows.length} rows and {visibleSquares.length} squares match “{search}”.
        </p>
      )}
      {(view === 'map' || view === 'both') && (
        <GardenCanvas
          area={area}
          beds={beds}
          rows={rows}
          squares={squares}
          describeBed={describeBed}
          describeRow={describeRow}
          describeSquare={describeSquare}
          bedClassName={bedClass}
          rowClassName={rowClass}
          squareClassName={squareClass}
          onSelectBed={onSelectBed}
          onSelectRow={onSelectRow}
          onSelectSquare={onSelectSquare}
        />
      )}
      {(view === 'list' || view === 'both') && (
        <section className="garden-layout-list" aria-labelledby="garden-layout-list-title">
          <h2 id="garden-layout-list-title" className="h4">
            Layout and actions
          </h2>
          <p className="text-muted">This list carries the same labels and opens the same actions as the map.</p>
          {beds.map((bed) => {
            const bedRows = visibleRows.filter((row) => row.bed === bed.pk)
            const bedSquares = visibleSquares.filter((square) => square.bed === bed.pk)
            if (normalizedSearch !== '' && !matches(`${bed.name} ${bed.kind}`) && bedRows.length === 0 && bedSquares.length === 0) return null
            return (
              <Card key={bed.pk} className="mb-3">
                <Card.Header className="d-flex justify-content-between align-items-center">
                  <span>
                    <strong>{bed.name}</strong> <Badge bg="secondary">{bed.kind.replace('_', ' ')}</Badge>
                  </span>
                  <Button size="sm" variant="outline-primary" onClick={() => onSelectBed(bed.pk)}>
                    Open bed actions
                  </Button>
                </Card.Header>
                <Card.Body>
                  <ul className="garden-geometry-list">
                    {bedRows.map((row) => (
                      <li key={`row-${row.pk}`}>
                        <span>{describeRow(row)}</span>
                        <Button size="sm" variant="link" onClick={() => onSelectRow(row.pk)}>
                          Open row actions
                        </Button>
                      </li>
                    ))}
                    {bedSquares.map((square) => (
                      <li key={`square-${square.pk}`}>
                        <span>{describeSquare(square)}</span>
                        <Button size="sm" variant="link" onClick={() => onSelectSquare(square.pk)}>
                          Open square actions
                        </Button>
                      </li>
                    ))}
                    {bedRows.length === 0 && bedSquares.length === 0 && <li>Unallocated bed space</li>}
                  </ul>
                </Card.Body>
              </Card>
            )
          })}
          <p>Paths and unallocated area space</p>
        </section>
      )}
    </>
  )
}

export { GardenWorkspace, LayoutEditModal }
