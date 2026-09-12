import React from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Card, Col, Form, Row, Table } from 'react-bootstrap'

import { addHealthCatalogValue, getHealthDiagnoses, getHealthObservationTypes, updateHealthCatalogValue } from './api/health'
import { CatalogSearch, DuplicateWarning, MergeDialog, MergedIntoNote, RetireButton, RetiredBadge, mergeChoices, retiredRowClass } from './catalog'
import { queryKeys, searchedKey } from './query'
import { CatalogRecordLabel } from './types/catalog'
import { HealthCatalogKind, HealthCatalogValue, HealthDiagnosisCategory } from './types/health'
import { errorsByField } from './utils'

const COLLECTIONS: Record<HealthCatalogKind, string> = {
  'observation-types': '/health/observation-types/',
  diagnoses: '/health/diagnoses/'
}

const TITLES: Record<HealthCatalogKind, string> = {
  'observation-types': 'Evidence types',
  diagnoses: 'Diagnoses'
}

// The five groupings a diagnosis is filed under. They are the server's own
// choices rather than records, which is why they are written here: nothing can
// retire one, and no route lists them.
const CATEGORY_LABELS: Record<HealthDiagnosisCategory, string> = {
  pest: 'Pest',
  disease: 'Disease',
  damage: 'Damage',
  vigor: 'Vigor or stress',
  other: 'Other'
}

const CATEGORIES = Object.keys(CATEGORY_LABELS) as Array<HealthDiagnosisCategory>

interface Merging {
  collection: string
  source: CatalogRecordLabel
  choices: Array<CatalogRecordLabel>
}

function nameOf(values: Array<HealthCatalogValue>, pk: number | null): string | null {
  if (pk == null) return null
  return values.find((value) => value.pk === pk)?.name ?? null
}

// For an evidence type a merge is onto every other one still in use; for a
// diagnosis it is onto every other one still in use under the same category,
// because the server refuses a cross-category merge as a reclassification and
// a picker offering one would make that refusal the operator's problem rather
// than the screen's. An evidence type carries no category, so the one filter
// says both.
function catalogMergeChoices(values: Array<HealthCatalogValue>, source: HealthCatalogValue): Array<CatalogRecordLabel> {
  return mergeChoices(
    values.filter((value) => value.category === source.category),
    source
  )
}

interface CatalogProps {
  kind: HealthCatalogKind
  //: The whole collection, which is what a merge may be onto and what the
  //: `Merged into` note reads a name out of. Both have to reach records the
  //: search is deliberately hiding.
  values: Array<HealthCatalogValue>
  //: What the rows are read from, narrowed by whatever is in the search box.
  found: Array<HealthCatalogValue>
  showRetired: boolean
  onMerge: (merging: Merging) => void
  onSaved: () => void
  onFailure: (message: string | null) => void
}

function Catalog({ kind, values, found, showRetired, onMerge, onSaved, onFailure }: CatalogProps) {
  const [code, setCode] = React.useState('')
  const [name, setName] = React.useState('')
  const [category, setCategory] = React.useState<HealthDiagnosisCategory>('pest')
  const collection = COLLECTIONS[kind]
  const grouped = kind === 'diagnoses'

  // Retirement is refused while something the catalog rule protects is in the
  // way, and a stable code is refused outright, so a failure names a field and
  // belongs on the screen rather than only in the global alert.
  async function save(write: () => Promise<unknown>) {
    onFailure(null)
    try {
      await write()
      onSaved()
    } catch (error) {
      const fields = errorsByField(error)
      onFailure(fields.active ?? fields.code ?? fields.name ?? fields.category ?? fields.form ?? 'The change could not be saved.')
    }
  }

  const create = useMutation({
    mutationFn: () => addHealthCatalogValue(kind, grouped ? { code, name, category, display_order: values.length } : { code, name, display_order: values.length })
  })
  const edit = useMutation({
    mutationFn: ({ pk, changes }: { pk: number; changes: Partial<HealthCatalogValue> }) => updateHealthCatalogValue(kind, pk, changes)
  })
  const visible = found.filter((value) => value.active || showRetired)

  return (
    <Card>
      <Card.Header>{TITLES[kind]}</Card.Header>
      <Card.Body>
        <Table size="sm">
          <thead>
            <tr>
              <th>Name</th>
              {grouped && <th>Category</th>}
              <th>Code</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {visible.map((value) => (
              <tr key={value.pk} className={retiredRowClass(value.active)}>
                <td>
                  <Form.Control
                    aria-label={`Name of ${value.code}`}
                    defaultValue={value.name}
                    onBlur={(event) => {
                      const next = event.target.value.trim()
                      if (next && next !== value.name) void save(() => edit.mutateAsync({ pk: value.pk, changes: { name: next } }))
                    }}
                  />
                  <RetiredBadge active={value.active} />
                  <MergedIntoNote into={nameOf(values, value.merged_into)} />
                </td>
                {/* The category is a field on the record rather than the
                    identity anything holds it by, so it stays editable: a
                    diagnosis filed under the wrong group is refiled, which is
                    the deliberate edit a merge refuses to make on its own. */}
                {grouped && (
                  <td>
                    <Form.Select
                      aria-label={`Category of ${value.name}`}
                      value={value.category}
                      onChange={(event) => {
                        const next = event.target.value as HealthDiagnosisCategory
                        if (next !== value.category) void save(() => edit.mutateAsync({ pk: value.pk, changes: { category: next } }))
                      }}
                    >
                      {CATEGORIES.map((option) => (
                        <option key={option} value={option}>
                          {CATEGORY_LABELS[option]}
                        </option>
                      ))}
                    </Form.Select>
                  </td>
                )}
                {/* The code is what a seeded default, a stored rule and every
                    report hold this setting by, so it is shown and never
                    offered for editing: a wrong one is merged away instead. */}
                <td>
                  <code>{value.code}</code>
                </td>
                <td className="text-nowrap">
                  {value.merged_into === null && (
                    <>
                      <Button
                        size="sm"
                        variant="outline-secondary"
                        onClick={() => onMerge({ collection, source: { pk: value.pk, label: value.name }, choices: catalogMergeChoices(values, value) })}
                      >
                        Merge
                      </Button>{' '}
                    </>
                  )}
                  <RetireButton active={value.active} saving={edit.isPending} onChange={(active) => void save(() => edit.mutateAsync({ pk: value.pk, changes: { active } }))} />
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
        <Row className="g-2">
          <Col>
            <Form.Control aria-label={`New ${TITLES[kind]} code`} placeholder="stable_code" value={code} onChange={(event) => setCode(event.target.value)} />
          </Col>
          {grouped && (
            <Col>
              <Form.Select aria-label="New diagnosis category" value={category} onChange={(event) => setCategory(event.target.value as HealthDiagnosisCategory)}>
                {CATEGORIES.map((option) => (
                  <option key={option} value={option}>
                    {CATEGORY_LABELS[option]}
                  </option>
                ))}
              </Form.Select>
            </Col>
          )}
          <Col>
            <Form.Control aria-label={`New ${TITLES[kind]} name`} placeholder="Display name" value={name} onChange={(event) => setName(event.target.value)} />
            {/* A diagnosis is checked within the category it would be filed
                under, because that is where a merge would be allowed. A
                warning naming a pest to somebody typing a disease would offer
                a merge the server then refuses. */}
            <DuplicateWarning collection={collection} name={name} scope={grouped ? { category } : {}} />
          </Col>
          <Col xs="auto">
            <Button
              disabled={!code || !name || create.isPending}
              onClick={() =>
                void save(async () => {
                  await create.mutateAsync()
                  setCode('')
                  setName('')
                })
              }
            >
              Add
            </Button>
          </Col>
        </Row>
      </Card.Body>
    </Card>
  )
}

function HealthCatalogsView() {
  const queryClient = useQueryClient()
  const [search, setSearch] = React.useState('')
  const [showRetired, setShowRetired] = React.useState(false)
  const [merging, setMerging] = React.useState<Merging | null>(null)
  const [failure, setFailure] = React.useState<string | null>(null)
  const { data: types = [] } = useQuery({ queryKey: queryKeys.health.types, queryFn: ({ signal }) => getHealthObservationTypes(signal) })
  const { data: diagnoses = [] } = useQuery({ queryKey: queryKeys.health.diagnoses, queryFn: ({ signal }) => getHealthDiagnoses(signal) })
  const { data: foundTypes = [] } = useQuery({
    queryKey: searchedKey(queryKeys.health.types, search),
    queryFn: ({ signal }) => getHealthObservationTypes(signal, search)
  })
  const { data: foundDiagnoses = [] } = useQuery({
    queryKey: searchedKey(queryKeys.health.diagnoses, search),
    queryFn: ({ signal }) => getHealthDiagnoses(signal, search)
  })
  // An evidence type and a diagnosis are named by every observation, and a
  // merge moves those observations onto another record, so what has moved on
  // is the whole of the health history rather than these two lists.
  const refresh = () => queryClient.invalidateQueries({ queryKey: queryKeys.health.all })
  const props = { showRetired, onMerge: setMerging, onSaved: refresh, onFailure: setFailure }

  return (
    <main className="container py-3">
      <div className="d-flex justify-content-between align-items-start">
        <div>
          <h1>Plant health setup</h1>
          <p>Maintain the evidence types an inspection is recorded under and the diagnoses assessed from it.</p>
        </div>
        <div className="d-flex align-items-center gap-3">
          <CatalogSearch id="health-catalog-search" onSearch={setSearch} label="Search evidence types and diagnoses" />
          <Form.Check type="switch" id="show-retired-health" label="Show retired" checked={showRetired} onChange={(event) => setShowRetired(event.target.checked)} />
        </div>
      </div>
      {failure && (
        <Alert variant="danger" onClose={() => setFailure(null)} dismissible>
          {failure}
        </Alert>
      )}
      {merging && (
        <MergeDialog
          collection={merging.collection}
          source={merging.source}
          choices={merging.choices}
          onMerged={() => {
            setMerging(null)
            refresh()
          }}
          onCancel={() => setMerging(null)}
        />
      )}
      <Row className="g-3">
        <Col lg={6}>
          <Catalog kind="observation-types" values={types} found={foundTypes} {...props} />
        </Col>
        <Col lg={6}>
          <Catalog kind="diagnoses" values={diagnoses} found={foundDiagnoses} {...props} />
        </Col>
      </Row>
    </main>
  )
}

export { CATEGORY_LABELS, HealthCatalogsView }
