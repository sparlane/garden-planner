import { CohortLossCause } from '../types/plantings'

// The cohort loss causes an operator may record, in the order the server lists
// them. `unspecified` is absent on purpose: it only describes losses taken
// before a cause was required, and the API rejects it on a new one.
const RECORDABLE_LOSS_CAUSES: Array<CohortLossCause> = ['failed', 'lost', 'culled', 'donated']

// Every cause a screen may have to name, including the history-only one.
const LOSS_CAUSE_LABELS: Record<CohortLossCause, string> = {
  failed: 'Failed',
  lost: 'Lost at stocktake',
  culled: 'Culled',
  donated: 'Donated',
  unspecified: 'Unspecified'
}

// Name a cause a report or a history row carries, including the blank a
// non-loss operation has.
function lossCauseLabel(cause: CohortLossCause | '' | null | undefined, fallback = '—'): string {
  return cause ? LOSS_CAUSE_LABELS[cause] : fallback
}

export { LOSS_CAUSE_LABELS, RECORDABLE_LOSS_CAUSES, lossCauseLabel }
