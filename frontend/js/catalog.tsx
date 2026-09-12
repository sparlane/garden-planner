import React from 'react'
import { Alert, Badge, Button, Form, Modal, Spinner } from 'react-bootstrap'
import { useQuery, useQueryClient } from '@tanstack/react-query'

import { checkCatalogDuplicates, correctCatalogRecord, mergeCatalogRecords, previewCatalogMerge, previewCatalogReplacement, replaceCatalogRecord } from './api/catalog'
import { queryKeys } from './query'
import { CatalogDuplicate, CatalogRecordLabel, CatalogReference, CatalogScope } from './types/catalog'
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

// A duplicate may only be merged onto a record it is interchangeable with,
// which is one still in use and not itself. Narrow further before calling it
// wherever something else files the record -- a variety's crop, a diagnosis's
// category -- because the server refuses a merge across those as a
// reclassification, and a picker offering one makes that refusal the
// operator's problem rather than the screen's.
function mergeChoices<Record extends CatalogRecord & { name: string }>(records: Array<Record>, source: Record): Array<CatalogRecordLabel> {
  return activeChoices(records)
    .filter((record) => record.pk !== source.pk)
    .map((record) => ({ pk: record.pk, label: record.name }))
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

interface ReferencedRecord {
  reference_source: string
  reference_fields: Array<string>
}

// Where a record came from, for the screens that maintain the catalog. A
// record the gardener made carries no source and says nothing here, which is
// the difference between a catalog somebody built and one that arrived. The
// set names itself rather than being labelled generically, because a catalog
// may arrive from a garden club or from another garden as readily as from the
// set that ships in the box, and which one it was is the useful half.
function ReferenceBadge({ record }: { record: ReferencedRecord }) {
  if (!record.reference_source) return null
  return (
    <Badge bg="light" text="dark" className="ms-1" title={`Installed from the ${record.reference_source} set rather than measured in this garden`}>
      {record.reference_source}
    </Badge>
  )
}

// A figure a reference set supplied and nobody has changed is not yet a fact
// about this garden -- reference day counts are printed for somebody else's
// climate -- so it reads more quietly than one measured here. Which fields
// those are comes from the server, because it compares what the set holds now
// against what the record says now, and neither is what the page was built
// from.
function referenceFigureClass(record: ReferencedRecord, field: string): string | undefined {
  return record.reference_fields.includes(field) ? 'text-body-secondary fst-italic' : undefined
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

interface CatalogSearchProps {
  id: string
  //: Given the words to search for once the typing has stopped. A state setter
  //: is what this expects: the control holds what is being typed and reports
  //: only what settles, so a name costs one request rather than one a letter.
  onSearch: (search: string) => void
  label?: string
}

// Searching is asked of the collection rather than of the list already
// fetched. The comparison is on the normalized form of a name, which is the
// same one the duplicate warning makes, and varieties and seed catalog entries
// are paginated, so a filter written here could neither agree with the warning
// nor reach the records the page did not carry.
function CatalogSearch({ id, onSearch, label = 'Search the catalog' }: CatalogSearchProps) {
  const [typed, setTyped] = React.useState('')
  const settled = useSettledValue(typed)
  React.useEffect(() => {
    onSearch(settled)
  }, [settled, onSearch])
  return (
    <Form.Group controlId={id} className="mb-2">
      <Form.Label className="visually-hidden">{label}</Form.Label>
      <Form.Control type="search" value={typed} placeholder={label} onChange={(event) => setTyped(event.target.value)} />
    </Form.Group>
  )
}

interface DuplicateWarningProps {
  collection: string
  //: The name being typed. A collection with no name typed on it leaves this
  //: out — a seed catalog entry is one supplier's variety and no more — and is
  //: answered from its scope alone, as soon as that scope is known.
  name?: string
  //: Where a duplicate has to be filed too: the parents it hangs off, and any
  //: value it is grouped under that is not a record. The check is skipped until
  //: all of it is known, because the server answers within it or not at all.
  scope?: CatalogScope
  //: The record being corrected, which is not a duplicate of itself.
  exclude?: number
  //: The field the collection calls its name.
  field?: string
}

// A picker nobody has chosen from reads as `Number('')`, which is NaN rather
// than undefined, and an unchosen select reads as an empty string. Sending
// either would earn a 400 and a global error alert for a check nobody asked
// for out loud, so the warning waits until the whole scope is known.
function scopeIsKnown(scope: CatalogScope): boolean {
  return Object.values(scope).every((value) => (typeof value === 'number' ? Number.isFinite(value) : Boolean(value)))
}

// A warning, never a refusal: these catalogs carry no unique constraint on a
// name on purpose, so what the catalog already holds is reported and what to do
// about it stays with the operator. It says nothing at all when there is
// nothing to say, because a control that is usually empty is one an operator
// reads when it is not.
function DuplicateWarning({ collection, name, scope = {}, exclude, field = 'name' }: DuplicateWarningProps) {
  const named = name !== undefined
  const settled = useSettledValue(name?.trim() ?? '')
  const scoped = scopeIsKnown(scope)
  const check = useQuery({
    queryKey: queryKeys.catalog.duplicates(collection, settled, scope, exclude),
    queryFn: ({ signal }) => checkCatalogDuplicates(collection, named ? field : null, settled, scope, exclude, signal),
    enabled: scoped && (!named || settled.length > 0)
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

interface ReferenceSetDocument {
  source: string
}

interface ReferenceSetControlsProps<Document extends ReferenceSetDocument, Result> {
  //: What the exported file is called, before the set's own name is put in
  //: front of it: `crop-catalog` becomes `my-garden-crop-catalog.json`.
  name: string
  onExport: () => Promise<Document>
  onImport: (payload: Document) => Promise<Result>
  //: What to say once a document is in. It reports what the catalog now holds
  //: rather than what changed, because installing a set twice is meant to be
  //: uneventful and a screen saying nothing would read as a failure.
  describe: (result: Result) => string
}

// Turn a set's name into something a file system will not argue with.
function referenceSetFilename(name: string, source: string): string {
  const stem = source
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '')
  return `${stem || 'garden'}-${name}.json`
}

// Carry a catalog out of this garden as a document, or bring one in. Both
// directions are one shape and one route, so a catalog exported here installs
// into the next garden along without anything in between having to agree about
// a format twice.
//
// The browser never validates the document and never merges it: the server
// decides what a set may say, adopts a name the gardener already typed rather
// than duplicating it, and leaves alone every figure they have measured and
// every record they have retired. Reading the file and handing it over whole is
// the whole of what happens here.
function ReferenceSetControls<Document extends ReferenceSetDocument, Result>({ name, onExport, onImport, describe }: ReferenceSetControlsProps<Document, Result>) {
  const chooser = React.useRef<HTMLInputElement>(null)
  const [busy, setBusy] = React.useState<'export' | 'import' | null>(null)
  const [failure, setFailure] = React.useState<string | null>(null)
  const [installed, setInstalled] = React.useState<string | null>(null)

  async function exportSet() {
    setBusy('export')
    setFailure(null)
    setInstalled(null)
    try {
      const payload = await onExport()
      // A blob rather than a link to the route itself, because the export is an
      // authenticated JSON request like every other and a bare anchor would
      // leave the browser to repeat it without one.
      const url = URL.createObjectURL(new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' }))
      const link = window.document.createElement('a')
      link.href = url
      link.download = referenceSetFilename(name, payload.source)
      link.click()
      URL.revokeObjectURL(url)
    } finally {
      setBusy(null)
    }
  }

  async function importSet(file: File) {
    setBusy('import')
    setFailure(null)
    setInstalled(null)
    try {
      // Parsing here is not validation, it is the difference between a file
      // that is not JSON at all and one the server has an opinion about: only
      // the first has nothing to send.
      const payload = JSON.parse(await file.text()) as Document
      setInstalled(describe(await onImport(payload)))
    } catch (error) {
      const fields = errorsByField(error)
      setFailure(fields.source ?? fields.form ?? `${file.name} could not be installed. It has to be a catalog exported from a garden.`)
    } finally {
      setBusy(null)
    }
  }

  return (
    // One slot on the screen that owns it: the two buttons side by side and
    // whatever they have to say underneath, so a long refusal wraps under the
    // controls rather than stretching the row they sit in.
    <div className="d-flex flex-column align-items-end gap-1">
      <div className="d-flex gap-2">
        <Button variant="outline-secondary" disabled={busy !== null} onClick={() => void exportSet()}>
          {busy === 'export' ? 'Exporting…' : 'Export catalog'}
        </Button>
        <Button variant="outline-secondary" disabled={busy !== null} onClick={() => chooser.current?.click()}>
          {busy === 'import' ? 'Installing…' : 'Import catalog'}
        </Button>
      </div>
      <Form.Control
        ref={chooser}
        type="file"
        accept="application/json,.json"
        className="d-none"
        onChange={(event) => {
          const input = event.target as HTMLInputElement
          const file = input.files?.[0]
          // Cleared so that choosing the same file twice still reports a
          // change, which is how a gardener re-installs an edited document.
          input.value = ''
          if (file) void importSet(file)
        }}
      />
      {failure && (
        <Alert variant="danger" className="mb-0 py-1 px-2 small text-end" onClose={() => setFailure(null)} dismissible>
          {failure}
        </Alert>
      )}
      {installed && (
        <Alert variant="success" className="mb-0 py-1 px-2 small text-end" onClose={() => setInstalled(null)} dismissible>
          {installed}
        </Alert>
      )}
    </div>
  )
}

export {
  CatalogRecord,
  CatalogSearch,
  CatalogValues,
  CorrectionDialog,
  DuplicateWarning,
  MergeDialog,
  MergedIntoNote,
  ReferenceBadge,
  ReferenceSetControls,
  ReplacedByNote,
  RetireButton,
  RetiredBadge,
  activeChoices,
  changedValues,
  mergeChoices,
  referenceFigureClass,
  retiredRowClass
}
