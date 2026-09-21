import { Workspace } from './types/workspace'

/**
 * Whether stock, costing, and traceability detail should be shown.
 *
 * True for every Nursery workspace, and for a Garden workspace whose
 * gardener has opted into Advanced. Mirrors `Workspace.is_advanced` on the
 * backend — use this instead of comparing `mode` and `garden_experience`
 * separately, so the rule lives in one place.
 */
function isAdvanced(workspace: Workspace): boolean {
  return workspace.mode === 'nursery' || workspace.garden_experience === 'advanced'
}

/**
 * Whether this workspace enters amounts in more than one currency.
 *
 * Mirrors `Workspace.multi_currency_enabled`. False hides every currency
 * input and files new records in `currency_code`; it never hides a currency
 * already recorded, which is still displayed and still refuses to be totalled
 * with another.
 */
function multiCurrency(workspace: Workspace): boolean {
  return workspace.multi_currency_enabled
}

export { isAdvanced, multiCurrency }
