import { ConversionPolicy, Workspace } from './types/workspace'

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

/**
 * How this workspace says a foreign amount is converted into its own currency.
 *
 * Mirrors `Workspace.conversion_policy`. It is what a recorded conversion must
 * claim -- the server refuses a rate recorded by any other method -- and never
 * a calculation: no rate is derived from it, and none is held anywhere. Every
 * rate is typed against the transaction it converts.
 */
function conversionPolicy(workspace: Workspace): ConversionPolicy {
  return workspace.conversion_policy
}

/**
 * Every treatment a line can be classified under, across the apps that have
 * one. Sales calls its unclassified state `unclassified` and purchasing and
 * inventory call theirs `unknown`; `''` is a line whose treatment is derived
 * from the rate rather than entered. Spelled out here so a typo in a caller is
 * a compile error rather than a silent `'0'`.
 */
type TaxTreatment = '' | 'standard' | 'zero_rated' | 'exempt' | 'out_of_scope' | 'unclassified' | 'unknown'

/**
 * The rate to open a line's tax box on, given the treatment it carries.
 *
 * Mirrors `workspaces.tax.unentered_tax_rate`, which the server applies to a
 * request that names no rate. Only a standard-rated supply carries one: a
 * zero-rated export, an exempt supply, something outside the tax and a line
 * nobody has classified yet are all `'0'`, and the database refuses the other
 * pairing. Pass no treatment where the line has no treatment to carry.
 *
 * It is a starting value and never a constraint — the box stays open, because
 * an import and an overseas invoice are ordinary.
 */
function defaultTaxRate(workspace: Workspace, treatment?: TaxTreatment): string {
  if (treatment !== undefined && treatment !== '' && treatment !== 'standard') return '0'
  return workspace.default_tax_rate
}

export { conversionPolicy, defaultTaxRate, isAdvanced, multiCurrency }
export type { TaxTreatment }
