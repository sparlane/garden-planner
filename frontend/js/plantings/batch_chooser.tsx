import React from 'react'
import { useQuery } from '@tanstack/react-query'
import { Form } from 'react-bootstrap'

import { getProductionBatches } from '../api/plantings'
import { queryKeys } from '../query'
import { NewBatchInline } from '../types/plantings'

const NEW_BATCH = 'new'

interface BatchChoice {
  batch?: number
  new_batch?: NewBatchInline
}

interface BatchChooserProps {
  variety?: number
  value: BatchChoice
  onChange: (choice: BatchChoice) => void
}

function isChoiceComplete(choice: BatchChoice): boolean {
  if (choice.batch !== undefined) {
    return true
  }
  return Boolean(choice.new_batch?.code.trim())
}

/**
 * Choose the production batch a new sowing joins.
 *
 * Only active batches growing the selected packet's variety can accept a
 * sowing, so the alternative is creating one inline rather than picking an
 * incompatible batch.
 */
function BatchChooser({ variety, value, onChange }: BatchChooserProps) {
  const creating = value.new_batch !== undefined
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
        <option value="">Choose a batch…</option>
        {batches.map((batch) => (
          <option key={batch.pk} value={batch.pk}>
            {batch.code} ({batch.seeds_sown} sown)
          </option>
        ))}
        <option value={NEW_BATCH}>Create a new batch…</option>
      </Form.Select>
      {creating && (
        <Form.Control
          className="mt-1"
          required
          maxLength={64}
          placeholder="New batch code"
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
