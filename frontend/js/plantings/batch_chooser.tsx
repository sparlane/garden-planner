import React from 'react'
import { useQuery } from '@tanstack/react-query'
import { Form } from 'react-bootstrap'

import { getProductionBatches } from '../api/plantings'
import { queryKeys } from '../query'
import { NewBatchInline } from '../types/plantings'
import { Workspace } from '../types/workspace'
import { isAdvanced } from '../workspace_mode'
import { chooseBatchOption, newBatchCodePlaceholder, newBatchOption } from './batch_terms'

const NEW_BATCH = 'new'

interface BatchChoice {
  batch?: number
  new_batch?: NewBatchInline
}

interface BatchChooserProps {
  variety?: number
  value: BatchChoice
  onChange: (choice: BatchChoice) => void
  workspace: Workspace
}

/**
 * Whether a choice is ready to submit.
 *
 * A generated code is only offered in Basic mode: Advanced still requires an
 * operator to type one, since that is the audience task 60 leaves untouched.
 */
function isChoiceComplete(choice: BatchChoice, workspace: Workspace): boolean {
  if (choice.batch !== undefined) {
    return true
  }
  if (choice.new_batch === undefined) {
    return false
  }
  return !isAdvanced(workspace) || Boolean(choice.new_batch.code.trim())
}

/**
 * Choose the production batch a new sowing joins.
 *
 * Only active batches growing the selected packet's variety can accept a
 * sowing, so the alternative is creating one inline rather than picking an
 * incompatible batch.
 */
function BatchChooser({ variety, value, onChange, workspace }: BatchChooserProps) {
  const creating = value.new_batch !== undefined
  const codeRequired = isAdvanced(workspace)
  const { data: batches = [] } = useQuery({
    queryKey: queryKeys.plantings.batches('active', variety ?? '', '', false),
    queryFn: ({ signal }) => getProductionBatches({ status: 'active', variety }, signal),
    enabled: variety !== undefined
  })

  function updateSelection(selected: string) {
    if (selected === NEW_BATCH) {
      onChange({ new_batch: { code: '' } })
    } else if (selected === '') {
      onChange({})
    } else {
      onChange({ batch: Number(selected) })
    }
  }

  if (variety === undefined) {
    return <span className="text-muted">Choose a seed packet first.</span>
  }

  return (
    <>
      <Form.Select aria-label="Production batch" value={creating ? NEW_BATCH : (value.batch ?? '')} onChange={(event) => updateSelection(event.target.value)}>
        <option value="">{chooseBatchOption(workspace)}</option>
        {batches.map((batch) => (
          <option key={batch.pk} value={batch.pk}>
            {batch.code} ({batch.seeds_sown} sown)
          </option>
        ))}
        <option value={NEW_BATCH}>{newBatchOption(workspace)}</option>
      </Form.Select>
      {creating && (
        <Form.Control
          className="mt-1"
          required={codeRequired}
          maxLength={64}
          placeholder={codeRequired ? newBatchCodePlaceholder(workspace) : 'Leave blank to generate one'}
          value={value.new_batch?.code ?? ''}
          onChange={(event) => onChange({ new_batch: { code: event.target.value } })}
        />
      )}
      {batches.length === 0 && !creating && <div className="text-muted small">No active batch grows this variety yet.</div>}
    </>
  )
}

export { BatchChooser, isChoiceComplete }
export type { BatchChoice }
