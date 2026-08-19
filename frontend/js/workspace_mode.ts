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

export { isAdvanced }
