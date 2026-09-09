import { CatalogDuplicateCheck, CatalogMergePreview, CatalogMergeResult, CatalogReplacementPreview, CatalogReplacementResult } from '../types/catalog'
import { csrfPatch, csrfPost, fetchAsJson } from '../utils'

// Families, plants, varieties and suppliers all merge through the one action
// the server puts on every mergeable collection, so these take the collection
// path rather than being written out four times per verb.
function previewCatalogMerge(collection: string, pk: number, into: number, signal?: AbortSignal): Promise<CatalogMergePreview> {
  return fetchAsJson<CatalogMergePreview>(`${collection}${pk}/merge/?into=${into}`, signal)
}

async function mergeCatalogRecords(collection: string, pk: number, into: number): Promise<CatalogMergeResult> {
  const response = await csrfPost(`${collection}${pk}/merge/`, { into })
  return response.json() as Promise<CatalogMergeResult>
}

function previewCatalogReplacement(collection: string, pk: number, signal?: AbortSignal): Promise<CatalogReplacementPreview> {
  return fetchAsJson<CatalogReplacementPreview>(`${collection}${pk}/replace/`, signal)
}

// Both corrections send only the fields that should read differently: the
// server carries the rest of the record over, so a screen cannot drop the half
// of it that has no control on the form.
async function replaceCatalogRecord<Record>(collection: string, pk: number, changes: object): Promise<CatalogReplacementResult<Record>> {
  const response = await csrfPost(`${collection}${pk}/replace/`, changes)
  return response.json() as Promise<CatalogReplacementResult<Record>>
}

async function correctCatalogRecord<Record>(collection: string, pk: number, changes: object): Promise<Record> {
  const response = await csrfPatch(`${collection}${pk}/`, changes)
  return response.json() as Promise<Record>
}

// The name goes under the field the collection calls it, because a tray model
// is named by an `identifier` and everything else by a `name`, and the scope is
// the parents a duplicate has to share — the server refuses the check without
// them rather than answering from another crop. A `null` field is a collection
// with no name typed on it, where those parents are the whole question.
function checkCatalogDuplicates(
  collection: string,
  field: string | null,
  name: string,
  scope: Record<string, number | undefined>,
  exclude: number | undefined,
  signal?: AbortSignal
): Promise<CatalogDuplicateCheck> {
  const params = new URLSearchParams()
  if (field !== null) params.set(field, name)
  for (const [key, value] of Object.entries(scope)) {
    if (value !== undefined) params.set(key, String(value))
  }
  if (exclude !== undefined) params.set('exclude', String(exclude))
  return fetchAsJson<CatalogDuplicateCheck>(`${collection}duplicates/?${params.toString()}`, signal)
}

// Every catalog collection takes the same `?search=`, because the comparison
// is the server's: it is on the normalized form of a name and it reaches the
// records a paginated collection did not send, neither of which a filter
// written in the browser can do. Blank searches for nothing rather than for
// the empty string, so an untouched box asks for the whole catalog.
function catalogSearchQuery(search?: string): string {
  return search ? `?search=${encodeURIComponent(search)}` : ''
}

export { catalogSearchQuery, checkCatalogDuplicates, correctCatalogRecord, mergeCatalogRecords, previewCatalogMerge, previewCatalogReplacement, replaceCatalogRecord }
