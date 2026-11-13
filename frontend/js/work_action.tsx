import React from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Form, Modal } from 'react-bootstrap'
import { acknowledgeWorkTask, actOnWorkTask } from './api/work'
import { queryKeys } from './query'
import { WorkTask } from './types/work'
import { errorsByField, fetchAsJson, localDatetimeInputValue, parseLocalDatetimeInput } from './utils'

interface ResultOption {
  app_label: string
  model: string
  object_id: number
  label: string
  url: string
}

const LABELS = { complete: 'Complete', skip: 'Skip', reopen: 'Reopen', snooze: 'Snooze' }
type TaskAction = keyof typeof LABELS

function TaskActionDialog({ task, action }: { task: WorkTask; action: TaskAction }) {
  const [show, setShow] = React.useState(false)
  const [reason, setReason] = React.useState('')
  const [until, setUntil] = React.useState('')
  const [kind, setKind] = React.useState('planting')
  const [search, setSearch] = React.useState('')
  const [page, setPage] = React.useState(1)
  const [result, setResult] = React.useState<ResultOption | null>(null)
  const id = React.useId()
  const cache = useQueryClient()
  const options = useQuery({
    queryKey: [...queryKeys.work.all, 'result-options', kind, search, page],
    queryFn: ({ signal }) =>
      fetchAsJson<{ results: Array<ResultOption>; next: string | null }>(`/work/tasks/result-options/?${new URLSearchParams({ kind, search, page: String(page) })}`, signal, true),
    enabled: show && action === 'complete'
  })
  const mutation = useMutation({
    mutationFn: async () => {
      const acknowledged = task.pk === null ? await acknowledgeWorkTask(task.key) : task
      return actOnWorkTask(acknowledged.pk as number, {
        action,
        reason,
        results: result ? [result] : [],
        until: action === 'snooze' ? parseLocalDatetimeInput(until)?.toISOString() : undefined,
        idempotency_key: crypto.randomUUID()
      })
    },
    onSuccess: () => {
      setShow(false)
      cache.invalidateQueries({ queryKey: queryKeys.work.all })
    }
  })
  const close = () => {
    if (!mutation.isPending) setShow(false)
  }
  return (
    <>
      <Button
        size="sm"
        variant={action === 'complete' ? 'success' : 'outline-secondary'}
        onClick={() => {
          mutation.reset()
          setReason('')
          setResult(null)
          setUntil(localDatetimeInputValue(new Date(Date.now() + 24 * 60 * 60 * 1000)))
          setShow(true)
        }}
      >
        {LABELS[action]}
      </Button>
      <Modal show={show} onHide={close} aria-labelledby={`${id}-title`}>
        <Form
          onSubmit={(event) => {
            event.preventDefault()
            mutation.mutate()
          }}
        >
          <Modal.Header closeButton={!mutation.isPending}>
            <Modal.Title id={`${id}-title`}>
              {LABELS[action]}: {task.title}
            </Modal.Title>
          </Modal.Header>
          <Modal.Body>
            {mutation.isError && <Alert variant="danger">{Object.values(errorsByField(mutation.error)).join(' ') || 'Could not update this reminder. Please try again.'}</Alert>}
            <Form.Group controlId={`${id}-reason`} className="mb-3">
              <Form.Label>{action === 'skip' || action === 'reopen' ? 'Reason (required)' : 'Notes (optional)'}</Form.Label>
              <Form.Control
                autoFocus
                as="textarea"
                required={action === 'skip' || action === 'reopen'}
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                disabled={mutation.isPending}
              />
            </Form.Group>
            {action === 'snooze' && (
              <Form.Group controlId={`${id}-until`}>
                <Form.Label>Remind me again (device local time)</Form.Label>
                <Form.Control required type="datetime-local" value={until} onChange={(event) => setUntil(event.target.value)} disabled={mutation.isPending} />
              </Form.Group>
            )}
            {action === 'complete' && (
              <>
                <p>Completing this reminder records your review only. Record planting, care, movement, or harvest separately, then optionally link that existing result.</p>
                <Form.Group controlId={`${id}-kind`} className="mb-3">
                  <Form.Label>Result kind</Form.Label>
                  <Form.Select
                    value={kind}
                    onChange={(event) => {
                      setKind(event.target.value)
                      setResult(null)
                      setPage(1)
                    }}
                  >
                    <option value="planting">Garden planting</option>
                    <option value="care">Care application</option>
                    <option value="move">Crop movement</option>
                    <option value="plant_move">Individual plant movement</option>
                    <option value="harvest">Harvest</option>
                  </Form.Select>
                </Form.Group>
                <Form.Group controlId={`${id}-search`} className="mb-3">
                  <Form.Label>Search existing results</Form.Label>
                  <Form.Control
                    value={search}
                    onChange={(event) => {
                      setSearch(event.target.value)
                      setPage(1)
                      setResult(null)
                    }}
                  />
                </Form.Group>
                {options.isPending ? (
                  <p role="status">Loading results…</p>
                ) : options.isError ? (
                  <Alert variant="danger">
                    Could not load results.{' '}
                    <Button variant="link" onClick={() => options.refetch()}>
                      Retry
                    </Button>{' '}
                    You can complete without a result.
                  </Alert>
                ) : (
                  <>
                    <Form.Group controlId={`${id}-result`}>
                      <Form.Label>Reviewed result (optional)</Form.Label>
                      <Form.Select
                        value={result?.object_id ?? ''}
                        onChange={(event) => setResult(options.data?.results.find((entry) => entry.object_id === Number(event.target.value)) ?? null)}
                      >
                        <option value="">No linked result</option>
                        {options.data?.results.map((entry) => (
                          <option key={entry.object_id} value={entry.object_id}>
                            {entry.label}
                          </option>
                        ))}
                      </Form.Select>
                    </Form.Group>
                    {options.data?.results.length === 0 && <p>No matching results. Record the work first, or complete without linking a result.</p>}
                    <div className="d-flex gap-2 mt-2">
                      <Button
                        variant="outline-secondary"
                        disabled={page === 1}
                        onClick={() => {
                          setPage(page - 1)
                          setResult(null)
                        }}
                      >
                        Previous results
                      </Button>
                      <span>Page {page}</span>
                      <Button
                        variant="outline-secondary"
                        disabled={!options.data?.next}
                        onClick={() => {
                          setPage(page + 1)
                          setResult(null)
                        }}
                      >
                        Next results
                      </Button>
                    </div>
                  </>
                )}
              </>
            )}
          </Modal.Body>
          <Modal.Footer>
            <Button variant="secondary" onClick={close} disabled={mutation.isPending}>
              Cancel
            </Button>
            <Button type="submit" disabled={mutation.isPending}>
              {mutation.isPending ? 'Saving…' : LABELS[action]}
            </Button>
          </Modal.Footer>
        </Form>
      </Modal>
    </>
  )
}

export { TaskActionDialog }
