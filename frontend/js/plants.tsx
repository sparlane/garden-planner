import 'bootstrap'
import 'bootstrap/dist/css/bootstrap.css'

import React from 'react'
import { Alert, Button, Form, Table } from 'react-bootstrap'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { addPlant, addPlantFamily, addPlantVariety, getPlantFamilies, getPlants, getPlantVarieties, updatePlant, updatePlantFamily, updatePlantVariety } from './api/plants'
import { queryKeys } from './query'
import { MaturityBasis, Plant, PlantCreate, PlantFamily, PlantFamilyCreate, PlantVariety, PlantVarietyCreate } from './types/plants'
import { ApiError } from './utils'

type Editor = { kind: 'family' | 'plant' | 'variety'; pk?: number; parentPk?: number }
type FieldErrors = Record<string, string>

const BASIS_LABELS: Record<MaturityBasis, string> = {
  seed: 'From seed',
  transplanting: 'From transplanting'
}

function errorsByField(error: unknown): FieldErrors {
  const body = error instanceof ApiError ? error.body : null
  if (!body || typeof body !== 'object') return { form: 'The changes could not be saved.' }
  const errors: FieldErrors = {}
  for (const [field, detail] of Object.entries(body as Record<string, unknown>)) {
    errors[field] = Array.isArray(detail) ? String(detail[0]) : String(detail)
  }
  return errors
}

function optionalNumber(value: string): number | null {
  return value === '' ? null : Number(value)
}

function inputNumber(value: number | null | undefined): string {
  return value === null || value === undefined ? '' : String(value)
}

function displayNumber(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : String(value)
}

function displayRange(minimum: number | null | undefined, maximum: number | null | undefined): string {
  if (minimum == null && maximum == null) return '—'
  if (minimum == null) return `Up to ${maximum}`
  if (maximum == null) return `${minimum}+`
  return `${minimum}–${maximum}`
}

interface SaveActionsProps {
  formId: string
  saving: boolean
  errors: FieldErrors
  onCancel: () => void
}

function SaveActions({ formId, saving, errors, onCancel }: SaveActionsProps) {
  function submit() {
    const form = document.getElementById(formId) as HTMLFormElement | null
    form?.requestSubmit()
  }

  return (
    <>
      <Button size="sm" type="button" disabled={saving} onClick={submit}>
        {saving ? 'Saving…' : 'Save'}
      </Button>{' '}
      <Button size="sm" variant="secondary" disabled={saving} onClick={onCancel}>
        Cancel
      </Button>
      {(errors.form || errors.non_field_errors || errors.detail) && (
        <Alert variant="danger" className="mt-2 mb-0 p-1">
          {errors.form || errors.non_field_errors || errors.detail}
        </Alert>
      )}
    </>
  )
}

interface InputProps {
  formId: string
  field: string
  errors: FieldErrors
  value: string
  onChange: (value: string) => void
  required?: boolean
  type?: string
}

function EditorInput({ formId, field, errors, value, onChange, required = false, type = 'text' }: InputProps) {
  return (
    <>
      <Form.Control
        size="sm"
        form={formId}
        type={type}
        required={required}
        aria-label={field.replaceAll('_', ' ')}
        value={value}
        isInvalid={field in errors}
        onChange={(event) => onChange(event.target.value)}
      />
      <Form.Control.Feedback type="invalid">{errors[field]}</Form.Control.Feedback>
    </>
  )
}

interface FamilyEditorProps {
  family?: PlantFamily
  onSave: (data: PlantFamilyCreate) => Promise<unknown>
  onDone: () => void
}

function FamilyEditor({ family, onSave, onDone }: FamilyEditorProps) {
  const formId = `family-editor-${family?.pk ?? 'new'}`
  const [name, setName] = React.useState(family?.name ?? '')
  const [notes, setNotes] = React.useState(family?.notes ?? '')
  const [saving, setSaving] = React.useState(false)
  const [errors, setErrors] = React.useState<FieldErrors>({})

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setSaving(true)
    setErrors({})
    try {
      await onSave({ name, notes })
      onDone()
    } catch (error) {
      setErrors(errorsByField(error))
    } finally {
      setSaving(false)
    }
  }

  return (
    <tr>
      <td>
        <Form onSubmit={submit} id={formId}></Form>
        <EditorInput formId={formId} field="name" errors={errors} value={name} onChange={setName} required />
      </td>
      <td colSpan={8}></td>
      <td>
        <EditorInput formId={formId} field="notes" errors={errors} value={notes} onChange={setNotes} />
      </td>
      <td>
        <SaveActions formId={formId} saving={saving} errors={errors} onCancel={onDone} />
      </td>
    </tr>
  )
}

interface CultivationFormState {
  name: string
  spacing: string
  interRowSpacing: string
  plantsPerSquareFoot: string
  germinationMin: string
  germinationMax: string
  maturityMin: string
  maturityMax: string
  notes: string
}

function cultivationState(value?: Plant | PlantVariety): CultivationFormState {
  return {
    name: value?.name ?? '',
    spacing: inputNumber(value?.spacing),
    interRowSpacing: inputNumber(value?.inter_row_spacing),
    plantsPerSquareFoot: inputNumber(value?.plants_per_square_foot),
    germinationMin: inputNumber(value?.germination_days_min),
    germinationMax: inputNumber(value?.germination_days_max),
    maturityMin: inputNumber(value?.maturity_days_min),
    maturityMax: inputNumber(value?.maturity_days_max),
    notes: value?.notes ?? ''
  }
}

function cultivationPayload(form: CultivationFormState) {
  return {
    name: form.name,
    spacing: optionalNumber(form.spacing),
    inter_row_spacing: optionalNumber(form.interRowSpacing),
    plants_per_square_foot: optionalNumber(form.plantsPerSquareFoot),
    germination_days_min: optionalNumber(form.germinationMin),
    germination_days_max: optionalNumber(form.germinationMax),
    maturity_days_min: optionalNumber(form.maturityMin),
    maturity_days_max: optionalNumber(form.maturityMax),
    notes: form.notes
  }
}

interface NumberCellProps {
  formId: string
  field: string
  errors: FieldErrors
  value: string
  onChange: (value: string) => void
}

function NumberCell(props: NumberCellProps) {
  return (
    <td>
      <EditorInput {...props} type="number" />
    </td>
  )
}

interface RangeCellProps {
  formId: string
  minimumField: string
  maximumField: string
  errors: FieldErrors
  minimum: string
  maximum: string
  onMinimum: (value: string) => void
  onMaximum: (value: string) => void
}

function RangeCell({ formId, minimumField, maximumField, errors, minimum, maximum, onMinimum, onMaximum }: RangeCellProps) {
  return (
    <td>
      <div className="d-flex gap-1">
        <EditorInput formId={formId} field={minimumField} errors={errors} value={minimum} onChange={onMinimum} type="number" />
        <EditorInput formId={formId} field={maximumField} errors={errors} value={maximum} onChange={onMaximum} type="number" />
      </div>
    </td>
  )
}

interface PlantEditorProps {
  plant?: Plant
  families: Array<PlantFamily>
  initialFamily: number
  onSave: (data: PlantCreate) => Promise<unknown>
  onDone: () => void
}

function PlantEditor({ plant, families, initialFamily, onSave, onDone }: PlantEditorProps) {
  const formId = `plant-editor-${plant?.pk ?? 'new'}`
  const [family, setFamily] = React.useState(String(plant?.family ?? initialFamily))
  const [basis, setBasis] = React.useState<MaturityBasis>(plant?.maturity_basis ?? 'seed')
  const [form, setForm] = React.useState(() => cultivationState(plant))
  const [saving, setSaving] = React.useState(false)
  const [errors, setErrors] = React.useState<FieldErrors>({})
  const update = (field: keyof CultivationFormState, value: string) => setForm((current) => ({ ...current, [field]: value }))

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setSaving(true)
    setErrors({})
    try {
      await onSave({ ...cultivationPayload(form), family: Number(family), maturity_basis: basis })
      onDone()
    } catch (error) {
      setErrors(errorsByField(error))
    } finally {
      setSaving(false)
    }
  }

  return (
    <tr>
      <td>
        <Form onSubmit={submit} id={formId}></Form>
        <Form.Select size="sm" form={formId} value={family} isInvalid={'family' in errors} onChange={(event) => setFamily(event.target.value)}>
          {families.map((value) => (
            <option key={value.pk} value={value.pk}>
              {value.name}
            </option>
          ))}
        </Form.Select>
        <Form.Control.Feedback type="invalid">{errors.family}</Form.Control.Feedback>
      </td>
      <td>
        <EditorInput formId={formId} field="name" errors={errors} value={form.name} onChange={(value) => update('name', value)} required />
      </td>
      <td></td>
      <NumberCell formId={formId} field="spacing" errors={errors} value={form.spacing} onChange={(value) => update('spacing', value)} />
      <NumberCell formId={formId} field="inter_row_spacing" errors={errors} value={form.interRowSpacing} onChange={(value) => update('interRowSpacing', value)} />
      <NumberCell formId={formId} field="plants_per_square_foot" errors={errors} value={form.plantsPerSquareFoot} onChange={(value) => update('plantsPerSquareFoot', value)} />
      <RangeCell
        formId={formId}
        minimumField="germination_days_min"
        maximumField="germination_days_max"
        errors={errors}
        minimum={form.germinationMin}
        maximum={form.germinationMax}
        onMinimum={(value) => update('germinationMin', value)}
        onMaximum={(value) => update('germinationMax', value)}
      />
      <RangeCell
        formId={formId}
        minimumField="maturity_days_min"
        maximumField="maturity_days_max"
        errors={errors}
        minimum={form.maturityMin}
        maximum={form.maturityMax}
        onMinimum={(value) => update('maturityMin', value)}
        onMaximum={(value) => update('maturityMax', value)}
      />
      <td>
        <Form.Select size="sm" form={formId} value={basis} isInvalid={'maturity_basis' in errors} onChange={(event) => setBasis(event.target.value as MaturityBasis)}>
          <option value="seed">From seed</option>
          <option value="transplanting">From transplanting</option>
        </Form.Select>
        <Form.Control.Feedback type="invalid">{errors.maturity_basis}</Form.Control.Feedback>
      </td>
      <td>
        <EditorInput formId={formId} field="notes" errors={errors} value={form.notes} onChange={(value) => update('notes', value)} />
      </td>
      <td>
        <SaveActions formId={formId} saving={saving} errors={errors} onCancel={onDone} />
      </td>
    </tr>
  )
}

interface VarietyEditorProps {
  variety?: PlantVariety
  plants: Array<Plant>
  families: Array<PlantFamily>
  initialPlant: number
  onSave: (data: PlantVarietyCreate) => Promise<unknown>
  onDone: () => void
}

function VarietyEditor({ variety, plants, families, initialPlant, onSave, onDone }: VarietyEditorProps) {
  const formId = `variety-editor-${variety?.pk ?? 'new'}`
  const [plant, setPlant] = React.useState(String(variety?.plant ?? initialPlant))
  const [basis, setBasis] = React.useState<MaturityBasis | ''>(variety?.maturity_basis ?? '')
  const [form, setForm] = React.useState(() => cultivationState(variety))
  const [saving, setSaving] = React.useState(false)
  const [errors, setErrors] = React.useState<FieldErrors>({})
  const update = (field: keyof CultivationFormState, value: string) => setForm((current) => ({ ...current, [field]: value }))
  const selectedPlant = plants.find((value) => value.pk === Number(plant))
  const familyName = families.find((value) => value.pk === selectedPlant?.family)?.name ?? '—'

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setSaving(true)
    setErrors({})
    try {
      await onSave({ ...cultivationPayload(form), plant: Number(plant), maturity_basis: basis || null })
      onDone()
    } catch (error) {
      setErrors(errorsByField(error))
    } finally {
      setSaving(false)
    }
  }

  return (
    <tr>
      <td>{familyName}</td>
      <td>
        <Form onSubmit={submit} id={formId}></Form>
        <Form.Select size="sm" form={formId} value={plant} isInvalid={'plant' in errors} onChange={(event) => setPlant(event.target.value)}>
          {plants.map((value) => (
            <option key={value.pk} value={value.pk}>
              {value.name}
            </option>
          ))}
        </Form.Select>
        <Form.Control.Feedback type="invalid">{errors.plant}</Form.Control.Feedback>
      </td>
      <td>
        <EditorInput formId={formId} field="name" errors={errors} value={form.name} onChange={(value) => update('name', value)} required />
      </td>
      <NumberCell formId={formId} field="spacing" errors={errors} value={form.spacing} onChange={(value) => update('spacing', value)} />
      <NumberCell formId={formId} field="inter_row_spacing" errors={errors} value={form.interRowSpacing} onChange={(value) => update('interRowSpacing', value)} />
      <NumberCell formId={formId} field="plants_per_square_foot" errors={errors} value={form.plantsPerSquareFoot} onChange={(value) => update('plantsPerSquareFoot', value)} />
      <RangeCell
        formId={formId}
        minimumField="germination_days_min"
        maximumField="germination_days_max"
        errors={errors}
        minimum={form.germinationMin}
        maximum={form.germinationMax}
        onMinimum={(value) => update('germinationMin', value)}
        onMaximum={(value) => update('germinationMax', value)}
      />
      <RangeCell
        formId={formId}
        minimumField="maturity_days_min"
        maximumField="maturity_days_max"
        errors={errors}
        minimum={form.maturityMin}
        maximum={form.maturityMax}
        onMinimum={(value) => update('maturityMin', value)}
        onMaximum={(value) => update('maturityMax', value)}
      />
      <td>
        <Form.Select size="sm" form={formId} value={basis} isInvalid={'maturity_basis' in errors} onChange={(event) => setBasis(event.target.value as MaturityBasis | '')}>
          <option value="">Inherit ({selectedPlant ? BASIS_LABELS[selectedPlant.maturity_basis] : 'plant default'})</option>
          <option value="seed">From seed</option>
          <option value="transplanting">From transplanting</option>
        </Form.Select>
        <Form.Control.Feedback type="invalid">{errors.maturity_basis}</Form.Control.Feedback>
      </td>
      <td>
        <EditorInput formId={formId} field="notes" errors={errors} value={form.notes} onChange={(value) => update('notes', value)} />
      </td>
      <td>
        <SaveActions formId={formId} saving={saving} errors={errors} onCancel={onDone} />
      </td>
    </tr>
  )
}

function PlantsView() {
  const queryClient = useQueryClient()
  const [editor, setEditor] = React.useState<Editor | null>(null)
  const { data: families = [] } = useQuery({ queryKey: queryKeys.plants.families, queryFn: ({ signal }) => getPlantFamilies(signal) })
  const { data: plants = [] } = useQuery({ queryKey: queryKeys.plants.plants, queryFn: ({ signal }) => getPlants(signal) })
  const { data: varieties = [] } = useQuery({ queryKey: queryKeys.plants.varieties, queryFn: ({ signal }) => getPlantVarieties(signal) })
  const refresh = () => queryClient.invalidateQueries({ queryKey: queryKeys.plants.all })
  const createFamily = useMutation({ mutationFn: addPlantFamily, onSuccess: refresh })
  const editFamily = useMutation({ mutationFn: ({ pk, data }: { pk: number; data: PlantFamilyCreate }) => updatePlantFamily(pk, data), onSuccess: refresh })
  const createPlant = useMutation({ mutationFn: addPlant, onSuccess: refresh })
  const editPlant = useMutation({ mutationFn: ({ pk, data }: { pk: number; data: PlantCreate }) => updatePlant(pk, data), onSuccess: refresh })
  const createVariety = useMutation({ mutationFn: addPlantVariety, onSuccess: refresh })
  const editVariety = useMutation({ mutationFn: ({ pk, data }: { pk: number; data: PlantVarietyCreate }) => updatePlantVariety(pk, data), onSuccess: refresh })
  const done = () => setEditor(null)
  const rows: Array<React.ReactNode> = []

  if (editor?.kind === 'family' && editor.pk === undefined) {
    rows.push(<FamilyEditor key="new-family" onSave={(data) => createFamily.mutateAsync(data)} onDone={done} />)
  }
  for (const family of families) {
    if (editor?.kind === 'family' && editor.pk === family.pk) {
      rows.push(<FamilyEditor key={`family-${family.pk}`} family={family} onSave={(data) => editFamily.mutateAsync({ pk: family.pk, data })} onDone={done} />)
    } else {
      rows.push(
        <tr key={`family-${family.pk}`}>
          <td>{family.name}</td>
          <td colSpan={8}></td>
          <td>{family.notes || '—'}</td>
          <td>
            <Button size="sm" variant="outline-primary" onClick={() => setEditor({ kind: 'plant', parentPk: family.pk })}>
              Add plant
            </Button>{' '}
            <Button size="sm" variant="outline-secondary" onClick={() => setEditor({ kind: 'family', pk: family.pk })}>
              Edit
            </Button>
          </td>
        </tr>
      )
    }
    if (editor?.kind === 'plant' && editor.pk === undefined && editor.parentPk === family.pk) {
      rows.push(<PlantEditor key="new-plant" families={families} initialFamily={family.pk} onSave={(data) => createPlant.mutateAsync(data)} onDone={done} />)
    }
    for (const plant of plants.filter((value) => value.family === family.pk)) {
      if (editor?.kind === 'plant' && editor.pk === plant.pk) {
        rows.push(
          <PlantEditor
            key={`plant-${plant.pk}`}
            plant={plant}
            families={families}
            initialFamily={family.pk}
            onSave={(data) => editPlant.mutateAsync({ pk: plant.pk, data })}
            onDone={done}
          />
        )
      } else {
        rows.push(
          <tr key={`plant-${plant.pk}`}>
            <td>{family.name}</td>
            <td>{plant.name}</td>
            <td></td>
            <td>{displayNumber(plant.spacing)}</td>
            <td>{displayNumber(plant.inter_row_spacing)}</td>
            <td>{displayNumber(plant.plants_per_square_foot)}</td>
            <td>{displayRange(plant.germination_days_min, plant.germination_days_max)}</td>
            <td>{displayRange(plant.maturity_days_min, plant.maturity_days_max)}</td>
            <td>{BASIS_LABELS[plant.maturity_basis]}</td>
            <td>{plant.notes || '—'}</td>
            <td>
              <Button size="sm" variant="outline-primary" onClick={() => setEditor({ kind: 'variety', parentPk: plant.pk })}>
                Add variety
              </Button>{' '}
              <Button size="sm" variant="outline-secondary" onClick={() => setEditor({ kind: 'plant', pk: plant.pk })}>
                Edit
              </Button>
            </td>
          </tr>
        )
      }
      if (editor?.kind === 'variety' && editor.pk === undefined && editor.parentPk === plant.pk) {
        rows.push(<VarietyEditor key="new-variety" plants={plants} families={families} initialPlant={plant.pk} onSave={(data) => createVariety.mutateAsync(data)} onDone={done} />)
      }
      for (const variety of varieties.filter((value) => value.plant === plant.pk)) {
        if (editor?.kind === 'variety' && editor.pk === variety.pk) {
          rows.push(
            <VarietyEditor
              key={`variety-${variety.pk}`}
              variety={variety}
              plants={plants}
              families={families}
              initialPlant={plant.pk}
              onSave={(data) => editVariety.mutateAsync({ pk: variety.pk, data })}
              onDone={done}
            />
          )
        } else {
          rows.push(
            <tr key={`variety-${variety.pk}`}>
              <td>{family.name}</td>
              <td>{plant.name}</td>
              <td>{variety.name}</td>
              <td>{displayNumber(variety.spacing)}</td>
              <td>{displayNumber(variety.inter_row_spacing)}</td>
              <td>{displayNumber(variety.plants_per_square_foot)}</td>
              <td>{displayRange(variety.germination_days_min, variety.germination_days_max)}</td>
              <td>{displayRange(variety.maturity_days_min, variety.maturity_days_max)}</td>
              <td>{variety.maturity_basis ? BASIS_LABELS[variety.maturity_basis] : `Inherit (${BASIS_LABELS[variety.effective_maturity_basis]})`}</td>
              <td>{variety.notes || '—'}</td>
              <td>
                <Button size="sm" variant="outline-secondary" onClick={() => setEditor({ kind: 'variety', pk: variety.pk })}>
                  Edit
                </Button>
              </td>
            </tr>
          )
        }
      }
    }
  }

  return (
    <>
      <div className="d-flex justify-content-between align-items-center mb-3">
        <div>
          <h2 className="mb-1">Plants</h2>
          <div className="text-muted">Variety values override plant defaults when provided.</div>
        </div>
        <Button onClick={() => setEditor({ kind: 'family' })}>Add family</Button>
      </div>
      <Table responsive hover size="sm" className="align-middle">
        <thead>
          <tr>
            <th>Family</th>
            <th>Plant</th>
            <th>Variety</th>
            <th>Spacing (mm)</th>
            <th>Row spacing (mm)</th>
            <th>Per sq ft</th>
            <th>Germination (days)</th>
            <th>Maturity (days)</th>
            <th>Maturity counted</th>
            <th>Notes</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </Table>
    </>
  )
}

export { PlantsView }
