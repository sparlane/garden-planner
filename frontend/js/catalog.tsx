import React from 'react'
import { Alert, Badge, Button, Form, Modal, Spinner } from 'react-bootstrap'
import { useQuery, useQueryClient } from '@tanstack/react-query'

import { checkCatalogDuplicates, correctCatalogRecord, mergeCatalogRecords, previewCatalogMerge, previewCatalogReplacement, replaceCatalogRecord } from './api/catalog'
import { queryKeys } from './query'
import { CatalogDuplicate, CatalogRecordLabel, CatalogReference } from './types/catalog'
import { errorsByField } from './utils'

interface CatalogRecord {
  pk: number
  active: boolean
}

// Catalog collections carry retired records too, because the same list names
// the entry an old planting or receipt points at. A selector asks for the
// choices worth offering, keeping whatever the record being edited already
// holds so saving an unrelated correction cannot repoint it.
function activeChoices<Record extends CatalogRecord>(records: Array<Record>, keep?: number | null): Array<Record> {
  return records.filter((record) => record.active || (keep != null && record.pk === keep))
}

function RetiredBadge({ active }: { active: boolean }) {
  if (active) return null
  return (
    <Badge bg="secondary" className="ms-1">
      Retired
    </Badge>
  )
}

interface RetireButtonProps {
  active: boolean
  saving?: boolean
  onChange: (active: boolean) => void
}

function RetireButton({ active, saving = false, onChange }: RetireButtonProps) {
  return (
    <Button size="sm" variant={active ? 'outline-danger' : 'outline-success'} disabled={saving} onClick={() => onChange(!active)}>
      {active ? 'Retire' : 'Restore'}
    </Button>
  )
}

function retiredRowClass(active: boolean): string | undefined {
  return active ? undefined : 'text-body-secondary'
}

function MergedIntoNote({ into }: { into: string | null }) {
  if (!into) return null
  return <div className="small text-body-secondary">Merged into {into}</div>
}

function ReplacedByNote({ by }: { by: string | null }) {
  if (!by) return null
  return <div className="small text-body-secondary">Replaced by {by}</div>
}

function referenceSummary(reference: CatalogReference): string {
  const examples = reference.examples.join(', ')
  if (reference.count > reference.examples.length) return `${examples}, and ${reference.count - reference.examples.length} more`
  return examples
}

// Held back until the typing stops, so the check runs on a name rather than on
// every prefix of one. A short delay also keeps the answer from arriving while
// the operator is still mid-word and reading as a verdict on half a name.
function useSettledValue<Value>(value: Value, delay = 300): Value {
  const [settled, setSettled] = React.useState(value)
  React.useEffect(() => {
    const timer = setTimeout(() => setSettled(value), delay)
    return () => clearTimeout(timer)
  }, [value, delay])
  return settled
}

function duplicateSummary(candidate: CatalogDuplicate): string {
  if (candidate.handoff) {
    const verb = candidate.handoff.relation === 'merged_into' ? 'was merged into' : 'was replaced by'
    return `${candidate.label} has ${candidate.reason} and ${verb} ${candidate.handoff.label}.`
  }
  if (!candidate.active) return `${candidate.label} has ${candidate.reason} and is retired.`
  return `${candidate.label} has ${candidate.reason}.`
}

interface DuplicateWarningProps {
  collection: string
  name: string
  //: The parents a duplicate has to share. The check is skipped until every one
  //: of them is known, because the server answers within them or not at all.
  scope?: Record<string, number | undefined>
  //: The record being renamed, which is not a duplicate of itself.
  exclude?: number
  //: The field the collection calls its name.
  field?: string
}

// A warning, never a refusal: these catalogs carry no unique constraint on a
// name on purpose, so what the catalog already holds is reported and what to do
// about it stays with the operator. It says nothing at all when there is
// nothing to say, because a control that is usually empty is one an operator
// reads when it is not.
function DuplicateWarning({ collection, name, scope = {}, exclude, field = 'name' }: DuplicateWarningProps) {
  const settled = useSettledValue(name.trim())
  // A picker that has not been chosen yet reads as `Number('')`, which is NaN
  // rather than undefined, and sending that would earn a 400 and a global error
  // alert for a check nobody asked for out loud.
  const scoped = Object.values(scope).every((value) => value !== undefined && Number.isFinite(value))
  const check = useQuery({
    queryKey: queryKeys.catalog.duplicates(collection, settled, scope, exclude),
    queryFn: ({ signal }) => checkCatalogDuplicates(collection, field, settled, scope, exclude, signal),
    enabled: settled.length > 0 && scoped
  })
  const candidates = check.data?.candidates ?? []
  if (candidates.length === 0) return null
  return (
    <div className="small text-warning-emphasis mt-1">
      {candidates.map((candidate) => (
        <div key={candidate.pk}>{duplicateSummary(candidate)}</div>
      ))}
    </div>
  )
}

interface MergeDialogProps {
  collection: string
  source: CatalogRecordLabel
  choices: Array<CatalogRecordLabel>
  onMerged: () => void
  onCancel: () => void
}

// A merge is the one catalog correction that touches records the operator is
// not looking at, so the preview is part of the control rather than an extra
// step: the survivor is chosen, the server says what would move onto it and
// what stands in the way, and only then is the merge offered.
function MergeDialog({ collection, source, choices, onMerged, onCancel }: MergeDialogProps) {
  const queryClient = useQueryClient()
  const [into, setInto] = React.useState<number | ''>('')
  const [merging, setMerging] = React.useState(false)
  const [failure, setFailure] = React.useState<string | null>(null)
  const preview = useQuery({
    queryKey: queryKeys.catalog.mergePreview(collection, source.pk, Number(into)),
    queryFn: ({ signal }) => previewCatalogMerge(collection, source.pk, Number(into), signal),
    enabled: into !== ''
  })
  const blockers = preview.data?.blockers ?? []
  const references = preview.data?.references ?? []

  async function merge() {
    if (into === '') return
    setMerging(true)
    setFailure(null)
    try {
      await mergeCatalogRecords(collection, source.pk, Number(into))
      // Every preview taken against either record now describes a catalog that
      // has moved on, including the ones the caller's own refresh knows nothing
      // about, so they go together rather than one dialog at a time.
      await queryClient.invalidateQueries({ queryKey: queryKeys.catalog.all })
      onMerged()
    } catch (error) {
      const fields = errorsByField(error)
      setFailure(fields.into ?? 'The records could not be merged.')
    } finally {
      setMerging(false)
    }
  }

  return (
    <Modal show onHide={onCancel}>
      <Modal.Header closeButton>
        <Modal.Title>Merge {source.label}</Modal.Title>
      </Modal.Header>
      <Modal.Body>
        <p className="text-body-secondary">
          Everything recorded against {source.label} moves onto the record you choose. {source.label} is kept and retired, so anything already written against it still reads the
          same.
        </p>
        <Form.Group controlId="merge-target">
          <Form.Label>Merge into</Form.Label>
          <Form.Select value={into} onChange={(event) => setInto(event.target.value === '' ? '' : Number(event.target.value))}>
            <option value="">Choose a record…</option>
            {choices.map((choice) => (
              <option key={choice.pk} value={choice.pk}>
                {choice.label}
              </option>
            ))}
          </Form.Select>
        </Form.Group>
        {preview.isFetching && (
          <div className="mt-3">
            <Spinner animation="border" size="sm" /> Checking what would move…
          </div>
        )}
        {preview.isSuccess && !preview.isFetching && (
          <div className="mt-3">
            {references.length === 0 ? (
              <p className="mb-0">Nothing is recorded against {source.label} yet.</p>
            ) : (
              <ul className="mb-0">
                {references.map((reference) => (
                  <li key={reference.relation}>
                    {reference.count} {reference.noun}: {referenceSummary(reference)}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
        {blockers.length > 0 && (
          <Alert variant="warning" className="mt-3 mb-0">
            <ul className="mb-0">
              {blockers.map((blocker) => (
                <li key={blocker}>{blocker}</li>
              ))}
            </ul>
          </Alert>
        )}
        {failure && (
          <Alert variant="danger" className="mt-3 mb-0">
            {failure}
          </Alert>
        )}
      </Modal.Body>
      <Modal.Footer>
        <Button variant="secondary" onClick={onCancel} disabled={merging}>
          Cancel
        </Button>
        <Button onClick={merge} disabled={merging || into === '' || !preview.isSuccess || blockers.length > 0}>
          {merging ? 'Merging…' : 'Merge'}
        </Button>
      </Modal.Footer>
    </Modal>
  )
}

type CatalogValues = Record<string, unknown>

interface CorrectionDialogProps {
  collection: string
  source: CatalogRecordLabel
  //: What the record says now, keyed by the field names the API uses.
  original: CatalogValues
  //: What the form says. Only the difference between the two is sent.
  values: CatalogValues
  title: string
  onSaved: (replaced: boolean) => void
  onCancel: () => void
  // `replacing` is what the dialog itself is about to do, so a form can say
  // something about a field that only holds on the replacement path — a
  // successor needing a name of its own, say — without asking the server the
  // same question a second time.
  children: (fieldErrors: Record<string, string>, replacing: boolean) => React.ReactNode
}

function changedValues(original: CatalogValues, values: CatalogValues): CatalogValues {
  const changes: CatalogValues = {}
  for (const [name, value] of Object.entries(values)) {
    if (value !== original[name]) changes[name] = value
  }
  return changes
}

// One control for both ways of correcting a catalog record, because an operator
// fixing a wrong entry should not have to know which one they are owed. The
// server decides: it says which fields the record's posted stock has frozen,
// and a change to one of those supersedes the record instead of rewriting it.
// The dialog says which is about to happen before the button is pressed, since
// the two leave the catalog looking very different afterwards.
function CorrectionDialog({ collection, source, original, values, title, onSaved, onCancel, children }: CorrectionDialogProps) {
  const queryClient = useQueryClient()
  const [saving, setSaving] = React.useState(false)
  const [failure, setFailure] = React.useState<string | null>(null)
  const [fieldErrors, setFieldErrors] = React.useState<Record<string, string>>({})
  const preview = useQuery({
    queryKey: queryKeys.catalog.replacementPreview(collection, source.pk),
    queryFn: ({ signal }) => previewCatalogReplacement(collection, source.pk, signal)
  })
  const changes = changedValues(original, values)
  const identityFields = preview.data?.identity_fields ?? []
  const blockers = preview.data?.blockers ?? []
  const stays = preview.data?.stays ?? []
  const replacing = (preview.data?.identity_locked ?? false) && identityFields.some((field) => field in changes)
  const nothingChanged = Object.keys(changes).length === 0

  async function save() {
    setSaving(true)
    setFailure(null)
    setFieldErrors({})
    try {
      if (replacing) {
        await replaceCatalogRecord(collection, source.pk, changes)
      } else {
        await correctCatalogRecord(collection, source.pk, changes)
      }
      // A replacement leaves a second record behind and a correction can change
      // what a record is called, so every preview taken against this collection
      // now describes a catalog that has moved on.
      await queryClient.invalidateQueries({ queryKey: queryKeys.catalog.all })
      onSaved(replacing)
    } catch (error) {
      const fields = errorsByField(error)
      const { replacement, non_field_errors: nonFieldErrors, ...rest } = fields
      setFieldErrors(rest)
      setFailure(replacement ?? nonFieldErrors ?? (Object.keys(rest).length ? null : 'The record could not be changed.'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal show onHide={onCancel} size="lg">
      <Modal.Header closeButton>
        <Modal.Title>{title}</Modal.Title>
      </Modal.Header>
      <Modal.Body>
        {children(fieldErrors, replacing)}
        {preview.isLoading && (
          <div className="mt-3">
            <Spinner animation="border" size="sm" /> Checking what has been recorded against it…
          </div>
        )}
        {preview.isSuccess && (
          <Alert variant={replacing ? 'warning' : 'secondary'} className="mt-3 mb-0">
            {replacing ? (
              <>
                <p>
                  Stock has been posted against {source.label}, so this saves a new entry carrying the change. {source.label} is kept and retired pointing at it, and everything
                  below stays there — it was recorded against what that entry said.
                </p>
                {stays.length === 0 ? (
                  <p className="mb-0">Nothing is recorded against it yet.</p>
                ) : (
                  <ul className="mb-0">
                    {stays.map((reference) => (
                      <li key={reference.relation}>
                        {reference.count} {reference.noun}: {referenceSummary(reference)}
                      </li>
                    ))}
                  </ul>
                )}
              </>
            ) : (
              <p className="mb-0">
                {preview.data.identity_locked
                  ? `Stock has been posted against ${source.label}, so changing what it names would save a new entry instead. These changes do not.`
                  : `Nothing has been posted against ${source.label} yet, so this corrects it in place.`}
              </p>
            )}
          </Alert>
        )}
        {blockers.length > 0 && (
          <Alert variant="warning" className="mt-3 mb-0">
            <ul className="mb-0">
              {blockers.map((blocker) => (
                <li key={blocker}>{blocker}</li>
              ))}
            </ul>
          </Alert>
        )}
        {failure && (
          <Alert variant="danger" className="mt-3 mb-0">
            {failure}
          </Alert>
        )}
      </Modal.Body>
      <Modal.Footer>
        <Button variant="secondary" onClick={onCancel} disabled={saving}>
          Cancel
        </Button>
        <Button onClick={save} disabled={saving || nothingChanged || !preview.isSuccess || (replacing && blockers.length > 0)}>
          {saving ? 'Saving…' : replacing ? 'Save as a new entry' : 'Save'}
        </Button>
      </Modal.Footer>
    </Modal>
  )
}

export {
  CatalogRecord,
  CatalogValues,
  CorrectionDialog,
  DuplicateWarning,
  MergeDialog,
  MergedIntoNote,
  ReplacedByNote,
  RetireButton,
  RetiredBadge,
  activeChoices,
  changedValues,
  retiredRowClass
}
