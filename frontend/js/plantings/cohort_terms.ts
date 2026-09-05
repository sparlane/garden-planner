import { CohortLifecycleState } from '../types/plantings'

// What each state of a block of anonymous stock is called on screen. Shared
// because two screens ask about it from opposite directions: the cohort
// register lists blocks by state, and the batch screen reports how much of one
// crop is standing in each. Naming them twice would let the two drift.
const COHORT_STATE_LABELS: Record<CohortLifecycleState, string> = {
  growing: 'Growing',
  available: 'Available',
  retained: 'Retained',
  depleted: 'Depleted'
}

export { COHORT_STATE_LABELS }
