import React from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Card, Col, Form, Row, Table } from 'react-bootstrap'

import { addGrowthCatalogValue, getGrowthStages, getPlantGrades, updateGrowthCatalogValue } from '../api/plantings'
import { CatalogSearch, DuplicateWarning, MergeDialog, MergedIntoNote, RetireButton, RetiredBadge, activeChoices, retiredRowClass } from '../catalog'
import { queryKeys, searchedKey } from '../query'
import { CatalogRecordLabel } from '../types/catalog'
import { GrowthCatalogKind, GrowthCatalogValue } from '../types/plantings'
import { errorsByField } from '../utils'

const COLLECTIONS: Record<GrowthCatalogKind, string> = {
  'growth-stages': '/plantings/growth-stages/',
  'plant-grades': '/plantings/plant-grades/'
}

const TITLES: Record<GrowthCatalogKind, string> = {
  'growth-stages': 'Growth stages',
  'plant-grades': 'Plant grades'
}

interface Merging {
  collection: string
  source: CatalogRecordLabel
  choices: Array<CatalogRecordLabel>
}

function nameOf(values: Array<GrowthCatalogValue>, pk: number | null): string | null {
  if (pk == null) return null
  return values.find((value) => value.pk === pk)?.name ?? null
}

// A setting can only be merged onto one it is interchangeable with, which for a
// flat catalog is every other one still in use. The retired ones are left out
// because merging into a retired record is refused, and the record being merged
// away is not a choice for itself.
function mergeChoices(values: Array<GrowthCatalogValue>, source: GrowthCatalogValue): Array<CatalogRecordLabel> {
  return activeChoices(values)
    .filter((value) => value.pk !== source.pk)
    .map((value) => ({ pk: value.pk, label: value.name }))
}

interface CatalogProps {
  kind: GrowthCatalogKind
  //: The whole collection, which is what a merge may be onto and what the
  //: `Merged into` note reads a name out of. Both have to reach records the
  //: search is deliberately hiding.
  values: Array<GrowthCatalogValue>
  //: What the rows are read from, narrowed by whatever is in the search box.
  found: Array<GrowthCatalogValue>
  showRetired: boolean
  onMerge: (merging: Merging) => void
  onSaved: () => void
  onFailure: (message: string | null) => void
}

function Catalog({ kind, values, found, showRetired, onMerge, onSaved, onFailure }: CatalogProps) {
  const [code, setCode] = React.useState('')
  const [name, setName] = React.useState('')
  const collection = COLLECTIONS[kind]
  const stages = kind === 'growth-stages'

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
      onFailure(fields.active ?? fields.code ?? fields.name ?? fields.form ?? 'The change could not be saved.')
    }
  }

  const create = useMutation({ mutationFn: () => addGrowthCatalogValue(kind, { code, name, display_order: values.length }) })
  const edit = useMutation({
    mutationFn: ({ pk, changes }: { pk: number; changes: Partial<GrowthCatalogValue> }) => updateGrowthCatalogValue(kind, pk, changes)
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
              <th>Code</th>
              {stages && <th>Target days</th>}
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
                {/* The code is what a seeded default, a stored rule and every
                    report hold this setting by, so it is shown and never
                    offered for editing: a wrong one is merged away instead. */}
                <td>
                  <code>{value.code}</code>
                </td>
                {stages && (
                  <td>
                    <Form.Control
                      aria-label={`Target days for ${value.name}`}
                      type="number"
                      min={1}
                      defaultValue={value.target_days ?? ''}
                      onBlur={(event) => {
                        const targetDays = event.target.value === '' ? null : Number(event.target.value)
                        if (targetDays !== value.target_days) void save(() => edit.mutateAsync({ pk: value.pk, changes: { target_days: targetDays } }))
                      }}
                    />
                  </td>
                )}
                <td className="text-nowrap">
                  {value.merged_into === null && (
                    <>
                      <Button
                        size="sm"
                        variant="outline-secondary"
                        onClick={() => onMerge({ collection, source: { pk: value.pk, label: value.name }, choices: mergeChoices(values, value) })}
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
          <Col>
            <Form.Control aria-label={`New ${TITLES[kind]} name`} placeholder="Display name" value={name} onChange={(event) => setName(event.target.value)} />
            <DuplicateWarning collection={collection} name={name} />
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

function GrowthCatalogsView() {
  const queryClient = useQueryClient()
  const [search, setSearch] = React.useState('')
  const [showRetired, setShowRetired] = React.useState(false)
  const [merging, setMerging] = React.useState<Merging | null>(null)
  const [failure, setFailure] = React.useState<string | null>(null)
  const { data: stages = [] } = useQuery({ queryKey: queryKeys.plantings.growthCatalogs.stages, queryFn: ({ signal }) => getGrowthStages(signal) })
  const { data: grades = [] } = useQuery({ queryKey: queryKeys.plantings.growthCatalogs.grades, queryFn: ({ signal }) => getPlantGrades(signal) })
  const { data: foundStages = [] } = useQuery({
    queryKey: searchedKey(queryKeys.plantings.growthCatalogs.stages, search),
    queryFn: ({ signal }) => getGrowthStages(signal, search)
  })
  const { data: foundGrades = [] } = useQuery({
    queryKey: searchedKey(queryKeys.plantings.growthCatalogs.grades, search),
    queryFn: ({ signal }) => getPlantGrades(signal, search)
  })
  // A stage or a grade is named by the register, the cohort screen and every
  // observation, and a merge moves those observations onto another record, so
  // what has moved on is the whole of the nursery rather than these two lists.
  const refresh = () => queryClient.invalidateQueries({ queryKey: queryKeys.plantings.all })
  const props = { showRetired, onMerge: setMerging, onSaved: refresh, onFailure: setFailure }

  return (
    <main className="container py-3">
      <div className="d-flex justify-content-between align-items-start">
        <div>
          <h1>Nursery growth setup</h1>
          <p>Maintain the operational stages and commercial grades used by observations and registers.</p>
        </div>
        <div className="d-flex align-items-center gap-3">
          <CatalogSearch id="growth-catalog-search" onSearch={setSearch} label="Search stages and grades" />
          <Form.Check type="switch" id="show-retired-growth" label="Show retired" checked={showRetired} onChange={(event) => setShowRetired(event.target.checked)} />
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
          <Catalog kind="growth-stages" values={stages} found={foundStages} {...props} />
        </Col>
        <Col lg={6}>
          <Catalog kind="plant-grades" values={grades} found={foundGrades} {...props} />
        </Col>
      </Row>
    </main>
  )
}

export { GrowthCatalogsView }
