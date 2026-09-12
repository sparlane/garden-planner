import React from 'react'
import { Alert, Button, Card, Form, Table } from 'react-bootstrap'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router'

import { getCropVarieties, getPlant, getPlantFamilies, getPlantFamily, getPlantVariety, getPlants, updatePlant, updatePlantFamily, updatePlantVariety } from '../api/plants'
import { DuplicateWarning, MergeDialog, MergedIntoNote, ReferenceBadge, RetireButton, RetiredBadge, activeChoices, mergeChoices, referenceFigureClass } from '../catalog'
import { queryKeys } from '../query'
import { MaturityBasis, Plant, PlantCreate, PlantFamily, PlantFamilyCreate, PlantVariety, PlantVarietyCreate } from '../types/plants'
import { errorsByField } from '../utils'
import {
  BASIS_LABELS,
  CULTIVATION_FIGURES,
  CropFigureNote,
  CultivationFigure,
  CultivationFormState,
  effectiveFigure,
  InheritingVarietiesNote,
  cultivationPayload,
  cultivationState,
  figureIsSet,
  figureText
} from './cultivation'

const COLLECTIONS = {
  family: '/plants/family/',
  plant: '/plants/plant/',
  variety: '/plants/variety/'
}

type FieldErrors = Record<string, string>

function recordErrors(error: unknown): FieldErrors {
  const fields = errorsByField(error)
  return Object.keys(fields).length > 0 ? fields : { form: 'The changes could not be saved.' }
}

interface CrumbsProps {
  family?: PlantFamily
  plant?: Plant
  here: string
}

function Crumbs({ family, plant, here }: CrumbsProps) {
  return (
    <nav aria-label="breadcrumb" className="small mb-1">
      <Link to="/plants">Plants</Link>
      {family && (
        <>
          {' / '}
          <Link to={`/plants/family/${family.pk}`}>{family.name}</Link>
        </>
      )}
      {plant && (
        <>
          {' / '}
          <Link to={`/plants/plant/${plant.pk}`}>{plant.name}</Link>
        </>
      )}
      {' / '}
      <span className="text-body-secondary">{here}</span>
    </nav>
  )
}

interface RecordHeaderProps {
  record: { name: string; active: boolean; merged_into: number | null; reference_source: string; reference_fields: Array<string> }
  mergedInto: string | null
  onRetire: (active: boolean) => void
  onMerge?: () => void
}

// The record's own page carries its corrections, because a gardener who has
// arrived at the wrong entry has arrived at the place to fix it: retiring it,
// merging it into the one it duplicates, or reading where it has already gone.
function RecordHeader({ record, mergedInto, onRetire, onMerge }: RecordHeaderProps) {
  return (
    <div className="d-flex justify-content-between align-items-start mb-3">
      <div>
        <h2 className="h4 mb-0">
          {record.name}
          <RetiredBadge active={record.active} />
          <ReferenceBadge record={record} />
        </h2>
        <MergedIntoNote into={mergedInto} />
      </div>
      <div className="d-flex gap-2">
        {onMerge && record.merged_into === null && (
          <Button size="sm" variant="outline-secondary" onClick={onMerge}>
            Merge
          </Button>
        )}
        <RetireButton active={record.active} onChange={onRetire} />
      </div>
    </div>
  )
}

interface FieldProps {
  label: string
  field: string
  errors: FieldErrors
  value: string
  onChange: (value: string) => void
  children?: React.ReactNode
  className?: string
}

function TextField({ label, field, errors, value, onChange, children, className }: FieldProps) {
  return (
    <Form.Group className="mb-3">
      <Form.Label className={className}>{label}</Form.Label>
      <Form.Control value={value} isInvalid={field in errors} onChange={(event) => onChange(event.target.value)} />
      <Form.Control.Feedback type="invalid">{errors[field]}</Form.Control.Feedback>
      {children}
    </Form.Group>
  )
}

interface FigureFieldProps {
  figure: CultivationFigure
  errors: FieldErrors
  form: CultivationFormState
  onChange: (field: string, value: string) => void
  className?: string
  children?: React.ReactNode
}

// One figure, however many boxes it takes. A range is two controls under one
// label because it is one figure a gardener either states or leaves to the
// crop, and splitting it into two labelled fields would invite half of one.
function FigureField({ figure, errors, form, onChange, className, children }: FigureFieldProps) {
  const fields = figure.maximumField ? [figure.field, figure.maximumField] : [figure.field]
  return (
    <Form.Group className="mb-3">
      <Form.Label className={className}>{figure.label}</Form.Label>
      <div className="d-flex gap-2">
        {fields.map((field) => (
          <React.Fragment key={field}>
            <Form.Control
              type="number"
              aria-label={`${figure.label} ${field.endsWith('_max') ? 'maximum' : 'minimum'}`}
              value={form[field]}
              isInvalid={field in errors}
              onChange={(event) => onChange(field, event.target.value)}
            />
            <Form.Control.Feedback type="invalid">{errors[field]}</Form.Control.Feedback>
          </React.Fragment>
        ))}
      </div>
      {children}
    </Form.Group>
  )
}

interface SaveRowProps {
  saving: boolean
  errors: FieldErrors
}

function SaveRow({ saving, errors }: SaveRowProps) {
  const message = errors.form || errors.non_field_errors || errors.detail
  return (
    <>
      {message && (
        <Alert variant="danger" className="py-2">
          {message}
        </Alert>
      )}
      <Button type="submit" disabled={saving}>
        {saving ? 'Saving…' : 'Save'}
      </Button>
    </>
  )
}

// Shared by the three pages: a form that reports which field the server
// rejected and clears the report on the next attempt, because a stale red box
// on a field somebody has since corrected reads as a second refusal.
function useRecordSave<Payload>(save: (data: Payload) => Promise<unknown>) {
  const [saving, setSaving] = React.useState(false)
  const [errors, setErrors] = React.useState<FieldErrors>({})
  const [saved, setSaved] = React.useState(false)

  async function submit(event: React.FormEvent, data: Payload) {
    event.preventDefault()
    setSaving(true)
    setErrors({})
    setSaved(false)
    try {
      await save(data)
      setSaved(true)
    } catch (error) {
      setErrors(recordErrors(error))
    } finally {
      setSaving(false)
    }
  }

  return { saving, errors, saved, submit }
}

// Fill a form when its record arrives, and never again. The query client
// refetches on window focus, so a form that reset itself whenever the record
// object changed would throw away whatever the gardener was in the middle of
// typing the moment they came back to the tab.
function useLoadedRecord<Record extends { pk: number }>(record: Record | undefined, fill: (record: Record) => void) {
  const loaded = React.useRef<number | null>(null)
  React.useEffect(() => {
    if (!record || loaded.current === record.pk) return
    loaded.current = record.pk
    fill(record)
  }, [record, fill])
}

function SavedNote({ saved }: { saved: boolean }) {
  if (!saved) return null
  return (
    <span className="ms-2 text-success small" role="status">
      Saved.
    </span>
  )
}

interface RetireFailureProps {
  message: string | null
  onClose: () => void
}

// Retirement is refused while something active still hangs off the record, and
// the refusal names what is in the way, so it belongs against the record
// rather than only in the global alert.
function RetireFailure({ message, onClose }: RetireFailureProps) {
  if (!message) return null
  return (
    <Alert variant="danger" dismissible onClose={onClose}>
      {message}
    </Alert>
  )
}

function useCatalogPage() {
  const queryClient = useQueryClient()
  const [merging, setMerging] = React.useState(false)
  const [retireError, setRetireError] = React.useState<string | null>(null)
  const refresh = () => queryClient.invalidateQueries({ queryKey: queryKeys.plants.all })

  async function retire(save: () => Promise<unknown>) {
    setRetireError(null)
    try {
      await save()
    } catch (error) {
      const fields = errorsByField(error)
      setRetireError(fields.active ?? 'The change could not be saved.')
    }
  }

  return { merging, setMerging, retireError, setRetireError, refresh, retire }
}

function PlantFamilyDetailView({ familyPk }: { familyPk: number }) {
  const page = useCatalogPage()
  const { data: family, isPending } = useQuery({
    queryKey: queryKeys.plants.family(familyPk),
    queryFn: ({ signal }) => getPlantFamily(familyPk, signal)
  })
  const { data: families = [] } = useQuery({ queryKey: queryKeys.plants.families, queryFn: ({ signal }) => getPlantFamilies(signal) })
  const { data: plants = [] } = useQuery({ queryKey: queryKeys.plants.plants, queryFn: ({ signal }) => getPlants(signal) })
  const edit = useMutation({ mutationFn: (data: Partial<PlantFamilyCreate>) => updatePlantFamily(familyPk, data), onSuccess: page.refresh })
  const [name, setName] = React.useState('')
  const [notes, setNotes] = React.useState('')
  const form = useRecordSave((data: Partial<PlantFamilyCreate>) => edit.mutateAsync(data))

  useLoadedRecord(family, (record) => {
    setName(record.name)
    setNotes(record.notes ?? '')
  })

  if (isPending) return <main className="container-fluid mt-3">Loading family…</main>
  if (!family) return <main className="container-fluid mt-3">Family not found.</main>

  const filed = plants.filter((plant) => plant.family === familyPk)
  const mergedInto = families.find((value) => value.pk === family.merged_into)?.name ?? null

  return (
    <main className="container-fluid mt-3">
      <Crumbs here={family.name} />
      <RecordHeader record={family} mergedInto={mergedInto} onMerge={() => page.setMerging(true)} onRetire={(active) => page.retire(() => edit.mutateAsync({ active }))} />
      <RetireFailure message={page.retireError} onClose={() => page.setRetireError(null)} />
      {page.merging && (
        <MergeDialog
          collection={COLLECTIONS.family}
          source={{ pk: family.pk, label: family.name }}
          choices={mergeChoices(families, family)}
          onMerged={() => {
            page.setMerging(false)
            page.refresh()
          }}
          onCancel={() => page.setMerging(false)}
        />
      )}
      <Card className="mb-3">
        <Card.Body>
          <Card.Title className="h6">Family details</Card.Title>
          <Form onSubmit={(event) => form.submit(event, { name, notes })}>
            <TextField label="Name" field="name" errors={form.errors} value={name} onChange={setName}>
              <DuplicateWarning collection={COLLECTIONS.family} name={name} exclude={family.pk} />
            </TextField>
            <TextField label="Notes" field="notes" errors={form.errors} value={notes} onChange={setNotes} />
            <SaveRow saving={form.saving} errors={form.errors} />
            <SavedNote saved={form.saved} />
          </Form>
        </Card.Body>
      </Card>
      <Card>
        <Card.Body>
          <Card.Title className="h6">Crops in this family</Card.Title>
          {filed.length === 0 ? (
            <div className="text-body-secondary">Nothing is filed under this family.</div>
          ) : (
            <ul className="list-unstyled mb-0">
              {filed.map((plant) => (
                <li key={plant.pk}>
                  <Link to={`/plants/plant/${plant.pk}`}>{plant.name}</Link>
                  <RetiredBadge active={plant.active} />
                </li>
              ))}
            </ul>
          )}
        </Card.Body>
      </Card>
    </main>
  )
}

function CropDetailView({ plantPk }: { plantPk: number }) {
  const page = useCatalogPage()
  const { data: plant, isPending } = useQuery({
    queryKey: queryKeys.plants.plant(plantPk),
    queryFn: ({ signal }) => getPlant(plantPk, signal)
  })
  const { data: families = [] } = useQuery({ queryKey: queryKeys.plants.families, queryFn: ({ signal }) => getPlantFamilies(signal) })
  const { data: plants = [] } = useQuery({ queryKey: queryKeys.plants.plants, queryFn: ({ signal }) => getPlants(signal) })
  const { data: varieties = [] } = useQuery({
    queryKey: queryKeys.plants.cropVarieties(plantPk),
    queryFn: ({ signal }) => getCropVarieties(plantPk, signal)
  })
  const edit = useMutation({ mutationFn: (data: Partial<PlantCreate>) => updatePlant(plantPk, data), onSuccess: page.refresh })
  const [form, setForm] = React.useState<CultivationFormState>(() => cultivationState())
  const [family, setFamily] = React.useState('')
  const [basis, setBasis] = React.useState<MaturityBasis>('seed')
  const save = useRecordSave((data: Partial<PlantCreate>) => edit.mutateAsync(data))
  const update = (field: string, value: string) => setForm((current) => ({ ...current, [field]: value }))

  useLoadedRecord(plant, (record) => {
    setForm(cultivationState(record))
    setFamily(String(record.family))
    setBasis(record.maturity_basis)
  })

  if (isPending) return <main className="container-fluid mt-3">Loading crop…</main>
  if (!plant) return <main className="container-fluid mt-3">Crop not found.</main>

  const mergedInto = plants.find((value) => value.pk === plant.merged_into)?.name ?? null

  return (
    <main className="container-fluid mt-3">
      <Crumbs family={families.find((value) => value.pk === plant.family)} here={plant.name} />
      <RecordHeader record={plant} mergedInto={mergedInto} onMerge={() => page.setMerging(true)} onRetire={(active) => page.retire(() => edit.mutateAsync({ active }))} />
      <RetireFailure message={page.retireError} onClose={() => page.setRetireError(null)} />
      {page.merging && (
        <MergeDialog
          collection={COLLECTIONS.plant}
          source={{ pk: plant.pk, label: plant.name }}
          choices={mergeChoices(
            plants.filter((value) => value.family === plant.family),
            plant
          )}
          onMerged={() => {
            page.setMerging(false)
            page.refresh()
          }}
          onCancel={() => page.setMerging(false)}
        />
      )}
      <Card className="mb-3">
        <Card.Body>
          <Card.Title className="h6">Planning figures</Card.Title>
          <Card.Subtitle className="mb-3 text-body-secondary small">
            These are what a variety of this crop is planned by wherever it says nothing of its own, so correcting one here moves every variety that leaves it blank. A figure shown
            in grey is still saying what the set it arrived with supplied rather than anything measured in this garden.
          </Card.Subtitle>
          <Form onSubmit={(event) => save.submit(event, { ...cultivationPayload(form), family: Number(family), maturity_basis: basis })}>
            <Form.Group className="mb-3">
              <Form.Label>Family</Form.Label>
              <Form.Select value={family} isInvalid={'family' in save.errors} onChange={(event) => setFamily(event.target.value)}>
                {activeChoices(families, plant.family).map((value) => (
                  <option key={value.pk} value={value.pk}>
                    {value.name}
                  </option>
                ))}
              </Form.Select>
              <Form.Control.Feedback type="invalid">{save.errors.family}</Form.Control.Feedback>
            </Form.Group>
            <TextField label="Name" field="name" errors={save.errors} value={form.name} onChange={(value) => update('name', value)}>
              <DuplicateWarning collection={COLLECTIONS.plant} name={form.name} scope={{ family: Number(family) }} exclude={plant.pk} />
            </TextField>
            {CULTIVATION_FIGURES.map((figure) => (
              <FigureField key={figure.field} figure={figure} errors={save.errors} form={form} onChange={update} className={referenceFigureClass(plant, figure.field)}>
                <InheritingVarietiesNote inheriting={varieties.filter((variety) => !figureIsSet(variety, figure)).length} total={varieties.length} />
              </FigureField>
            ))}
            <Form.Group className="mb-3">
              <Form.Label className={referenceFigureClass(plant, 'maturity_basis')}>Maturity counted</Form.Label>
              <Form.Select value={basis} isInvalid={'maturity_basis' in save.errors} onChange={(event) => setBasis(event.target.value as MaturityBasis)}>
                <option value="seed">{BASIS_LABELS.seed}</option>
                <option value="transplanting">{BASIS_LABELS.transplanting}</option>
              </Form.Select>
              <Form.Control.Feedback type="invalid">{save.errors.maturity_basis}</Form.Control.Feedback>
              <InheritingVarietiesNote inheriting={varieties.filter((variety) => variety.maturity_basis === null).length} total={varieties.length} />
            </Form.Group>
            <TextField label="Notes" field="notes" errors={save.errors} value={form.notes} onChange={(value) => update('notes', value)} />
            <SaveRow saving={save.saving} errors={save.errors} />
            <SavedNote saved={save.saved} />
          </Form>
        </Card.Body>
      </Card>
      <Card>
        <Card.Body>
          <Card.Title className="h6">Varieties of this crop</Card.Title>
          {varieties.length === 0 ? (
            <div className="text-body-secondary">No varieties are filed under this crop.</div>
          ) : (
            <Table responsive size="sm" className="mb-0 align-middle">
              <thead>
                <tr>
                  <th>Variety</th>
                  <th>What it says of its own</th>
                </tr>
              </thead>
              <tbody>
                {varieties.map((variety) => {
                  const stated = CULTIVATION_FIGURES.filter((figure) => figureIsSet(variety, figure))
                  return (
                    <tr key={variety.pk}>
                      <td>
                        <Link to={`/plants/variety/${variety.pk}`}>{variety.name}</Link>
                        <RetiredBadge active={variety.active} />
                      </td>
                      <td>
                        {stated.length === 0 && variety.maturity_basis === null ? (
                          <span className="text-body-secondary">Nothing — planned entirely by {plant.name}.</span>
                        ) : (
                          [
                            ...stated.map((figure) => `${figure.label} ${figureText(variety, figure)}`),
                            ...(variety.maturity_basis === null ? [] : [BASIS_LABELS[variety.maturity_basis]])
                          ].join(', ')
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </Table>
          )}
        </Card.Body>
      </Card>
    </main>
  )
}

// What the variety is planned by, figure by figure, as the server resolved it.
// This is the half the hierarchical screen could not show: a blank cell there
// reads as a figure nobody has recorded, when every date the variety is grown
// to has already been worked out from its crop's.
// A figure neither the variety nor its crop states is nobody's, and saying it
// was the variety's would read as a decision somebody made.
function whose(entry: { inherited: boolean; stated: boolean }, crop: string): string {
  if (!entry.stated) return 'Nobody has recorded it'
  return entry.inherited ? crop : 'This variety'
}

function InForceTable({ variety, crop }: { variety: PlantVariety; crop: Plant | undefined }) {
  const cropName = crop?.name ?? 'its crop'
  return (
    <Table responsive size="sm" className="mb-0 align-middle">
      <thead>
        <tr>
          <th>Figure</th>
          <th>In force</th>
          <th>Whose</th>
        </tr>
      </thead>
      <tbody>
        {CULTIVATION_FIGURES.map((figure) => {
          const entry = effectiveFigure(variety, figure)
          return (
            <tr key={figure.field}>
              <td>{figure.label}</td>
              <td>{entry.text}</td>
              <td className="text-body-secondary">{whose(entry, cropName)}</td>
            </tr>
          )
        })}
        <tr>
          <td>Maturity counted</td>
          <td>{BASIS_LABELS[variety.effective.maturity_basis.value]}</td>
          <td className="text-body-secondary">{variety.effective.maturity_basis.inherited ? cropName : 'This variety'}</td>
        </tr>
      </tbody>
    </Table>
  )
}

function PlantVarietyDetailView({ varietyPk }: { varietyPk: number }) {
  const page = useCatalogPage()
  const { data: variety, isPending } = useQuery({
    queryKey: queryKeys.plants.variety(varietyPk),
    queryFn: ({ signal }) => getPlantVariety(varietyPk, signal)
  })
  const { data: families = [] } = useQuery({ queryKey: queryKeys.plants.families, queryFn: ({ signal }) => getPlantFamilies(signal) })
  const { data: plants = [] } = useQuery({ queryKey: queryKeys.plants.plants, queryFn: ({ signal }) => getPlants(signal) })
  const { data: siblings = [] } = useQuery({
    queryKey: queryKeys.plants.cropVarieties(variety?.plant ?? 0),
    queryFn: ({ signal }) => getCropVarieties(variety?.plant ?? 0, signal),
    enabled: variety !== undefined
  })
  const edit = useMutation({ mutationFn: (data: Partial<PlantVarietyCreate>) => updatePlantVariety(varietyPk, data), onSuccess: page.refresh })
  const [form, setForm] = React.useState<CultivationFormState>(() => cultivationState())
  const [plant, setPlant] = React.useState('')
  const [basis, setBasis] = React.useState<MaturityBasis | ''>('')
  const save = useRecordSave((data: Partial<PlantVarietyCreate>) => edit.mutateAsync(data))
  const update = (field: string, value: string) => setForm((current) => ({ ...current, [field]: value }))

  useLoadedRecord(variety, (record) => {
    setForm(cultivationState(record))
    setPlant(String(record.plant))
    setBasis(record.maturity_basis ?? '')
  })

  if (isPending) return <main className="container-fluid mt-3">Loading variety…</main>
  if (!variety) return <main className="container-fluid mt-3">Variety not found.</main>

  // The crop the form currently names rather than the one the variety was
  // saved under, so that choosing a different crop moves the guidance with it:
  // what a blank figure would mean is a fact about the crop underneath it.
  const chosen = plants.find((value) => value.pk === Number(plant))
  const saved = plants.find((value) => value.pk === variety.plant)
  const mergedInto = siblings.find((value) => value.pk === variety.merged_into)?.name ?? null

  return (
    <main className="container-fluid mt-3">
      <Crumbs family={families.find((value) => value.pk === saved?.family)} plant={saved} here={variety.name} />
      <RecordHeader record={variety} mergedInto={mergedInto} onMerge={() => page.setMerging(true)} onRetire={(active) => page.retire(() => edit.mutateAsync({ active }))} />
      <RetireFailure message={page.retireError} onClose={() => page.setRetireError(null)} />
      {page.merging && (
        <MergeDialog
          collection={COLLECTIONS.variety}
          source={{ pk: variety.pk, label: variety.name }}
          choices={mergeChoices(siblings, variety)}
          onMerged={() => {
            page.setMerging(false)
            page.refresh()
          }}
          onCancel={() => page.setMerging(false)}
        />
      )}
      <Card className="mb-3">
        <Card.Body>
          <Card.Title className="h6">What this variety is planned by</Card.Title>
          <Card.Subtitle className="mb-3 text-body-secondary small">
            A figure this variety leaves blank is not one nobody has recorded: every sowing and maturity date is already worked out from its crop&apos;s. Those are shown in grey
            below.
          </Card.Subtitle>
          <InForceTable variety={variety} crop={saved} />
        </Card.Body>
      </Card>
      <Card>
        <Card.Body>
          <Card.Title className="h6">Variety details</Card.Title>
          <Form onSubmit={(event) => save.submit(event, { ...cultivationPayload(form), plant: Number(plant), maturity_basis: basis || null })}>
            <Form.Group className="mb-3">
              <Form.Label>Crop</Form.Label>
              <Form.Select value={plant} isInvalid={'plant' in save.errors} onChange={(event) => setPlant(event.target.value)}>
                {activeChoices(plants, variety.plant).map((value) => (
                  <option key={value.pk} value={value.pk}>
                    {value.name}
                  </option>
                ))}
              </Form.Select>
              <Form.Control.Feedback type="invalid">{save.errors.plant}</Form.Control.Feedback>
            </Form.Group>
            <TextField label="Name" field="name" errors={save.errors} value={form.name} onChange={(value) => update('name', value)}>
              <DuplicateWarning collection={COLLECTIONS.variety} name={form.name} scope={{ plant: Number(plant) }} exclude={variety.pk} />
            </TextField>
            {CULTIVATION_FIGURES.map((figure) => (
              <FigureField key={figure.field} figure={figure} errors={save.errors} form={form} onChange={update} className={referenceFigureClass(variety, figure.field)}>
                <CropFigureNote figure={figure} crop={chosen} form={form} />
              </FigureField>
            ))}
            <Form.Group className="mb-3">
              <Form.Label>Maturity counted</Form.Label>
              <Form.Select value={basis} isInvalid={'maturity_basis' in save.errors} onChange={(event) => setBasis(event.target.value as MaturityBasis | '')}>
                <option value="">Inherit{chosen ? ` (${BASIS_LABELS[chosen.maturity_basis]})` : ''}</option>
                <option value="seed">{BASIS_LABELS.seed}</option>
                <option value="transplanting">{BASIS_LABELS.transplanting}</option>
              </Form.Select>
              <Form.Control.Feedback type="invalid">{save.errors.maturity_basis}</Form.Control.Feedback>
            </Form.Group>
            <TextField label="Notes" field="notes" errors={save.errors} value={form.notes} onChange={(value) => update('notes', value)} />
            <SaveRow saving={save.saving} errors={save.errors} />
            <SavedNote saved={save.saved} />
          </Form>
        </Card.Body>
      </Card>
    </main>
  )
}

export { CropDetailView, PlantFamilyDetailView, PlantVarietyDetailView }
