import { getSerializedUnit, getSerializedUnits, getSerializedUnitsByCode } from '../api/inventory'
import { resolveLabel } from '../api/labels'
import { SerializedInventoryUnit } from '../types/inventory'
import { LabelIdentity } from '../types/labels'

interface CodeLookup {
  unit?: SerializedInventoryUnit
  target?: LabelIdentity
  message: string
}

// Resolution deliberately has no placement gates: an absent or retired pot
// still has a history somebody holding its number needs to reach.
async function findPotByCode(value: string): Promise<CodeLookup> {
  const typed = value.trim()
  if (!typed) return { message: 'Enter a pot number or code.' }
  if (/^#?\d+$/.test(typed)) {
    const unit = await findPotByNumber(Number(typed.replace('#', '')))
    return { unit, message: unit ? '' : 'No pot has been issued that number.' }
  }
  const resolution = await resolveLabel(typed)
  if (resolution.status === 'unknown') {
    // Asset codes match fragments; never guess which matching pot was meant.
    const matches = await getSerializedUnitsByCode(typed)
    const exact = matches.find((candidate) => candidate.asset_code.toUpperCase() === typed.toUpperCase())
    if (!exact && matches.length > 1) {
      return { message: `${matches.length} pots match ${typed}. Type or scan the whole code.` }
    }
    const unit = exact ?? matches[0]
    return { unit, message: unit ? '' : `No label or pot carries the code ${typed}.` }
  }
  if (resolution.status !== 'active' || !resolution.target) return { message: resolution.message }
  if (resolution.target.target_type !== 'inventoryunit') return { target: resolution.target, message: '' }
  return { unit: await getSerializedUnit(resolution.target.object_id), message: '' }
}

// Use the range filter, not a detail request: an unissued number is an empty
// answer, not an HTTP error. Validate before sending a value to the API.
async function findPotByNumber(number: number, signal?: AbortSignal): Promise<SerializedInventoryUnit | undefined> {
  if (!Number.isSafeInteger(number) || number <= 0) return undefined
  const [found] = await getSerializedUnits({ number_from: number, number_to: number }, signal)
  return found
}

export { findPotByCode, findPotByNumber }
