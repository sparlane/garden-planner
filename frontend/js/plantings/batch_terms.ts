import { Workspace } from '../types/workspace'
import { ProductionBatchStatus } from '../types/plantings'

/**
 * Garden-mode vocabulary for `ProductionBatch`.
 *
 * The model, API, and route stay "batch" everywhere — change 2 asks only for
 * presentation, retaining the same underlying identity and links. A Nursery
 * workspace always reads "batch", regardless of `garden_experience`: this is
 * the Garden framing, not the detail level.
 *
 * "Planting cycle" rather than "crop": the batch table already has a "Crop"
 * column naming the plant/variety being grown, so reusing that word for the
 * batch identity itself would name two different things the same thing on
 * one screen. The todo names both as acceptable Garden vocabulary.
 */

const STATUS_LABELS: Record<ProductionBatchStatus, string> = {
  planned: 'Planned',
  active: 'Active',
  output_finalized: 'Output finalized',
  completed: 'Completed',
  cancelled: 'Cancelled'
}

// Only output_finalized reads oddly for a gardener; the rest carry over.
const GARDEN_STATUS_LABELS: Record<ProductionBatchStatus, string> = {
  ...STATUS_LABELS,
  output_finalized: 'Harvest complete'
}

function isGarden(workspace: Workspace): boolean {
  return workspace.mode === 'garden'
}

function batchCodeLabel(workspace: Workspace): string {
  return isGarden(workspace) ? 'Planting cycle code' : 'Batch code'
}

function newBatchCta(workspace: Workspace): string {
  return isGarden(workspace) ? 'New planting cycle' : 'New batch'
}

function createBatchCta(workspace: Workspace): string {
  return isGarden(workspace) ? 'Create planting cycle' : 'Create batch'
}

function addBatchCta(workspace: Workspace): string {
  return isGarden(workspace) ? 'Add planting cycle' : 'Add batch'
}

function batchListHeading(workspace: Workspace): string {
  return isGarden(workspace) ? 'Planting cycles' : 'Production batches'
}

function batchNavLabel(workspace: Workspace): string {
  return isGarden(workspace) ? 'Planting cycles' : 'Batches'
}

function backToBatchListLink(workspace: Workspace): string {
  return isGarden(workspace) ? '← All planting cycles' : '← All batches'
}

function chooseBatchOption(workspace: Workspace): string {
  return isGarden(workspace) ? 'Choose a planting cycle…' : 'Choose a batch…'
}

function newBatchOption(workspace: Workspace): string {
  return isGarden(workspace) ? 'Create a new planting cycle…' : 'Create a new batch…'
}

function newBatchCodePlaceholder(workspace: Workspace): string {
  return isGarden(workspace) ? 'New planting cycle code' : 'New batch code'
}

function batchStatusLabel(workspace: Workspace, status: ProductionBatchStatus): string {
  return (isGarden(workspace) ? GARDEN_STATUS_LABELS : STATUS_LABELS)[status]
}

function batchStatusLabels(workspace: Workspace): Record<ProductionBatchStatus, string> {
  return isGarden(workspace) ? GARDEN_STATUS_LABELS : STATUS_LABELS
}

export {
  addBatchCta,
  backToBatchListLink,
  batchCodeLabel,
  batchListHeading,
  batchNavLabel,
  batchStatusLabel,
  batchStatusLabels,
  chooseBatchOption,
  createBatchCta,
  newBatchCodePlaceholder,
  newBatchCta,
  newBatchOption
}
