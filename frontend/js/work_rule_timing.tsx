import React from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Form, Modal } from 'react-bootstrap'
import { getPlants, getPlantVarieties } from './api/plants'
import { updateWorkRule } from './api/work'
import { queryKeys } from './query'
import { WorkRule } from './types/work'
import { errorsByField } from './utils'

function RuleTiming({ rule }: { rule: WorkRule }) {
  const [show, setShow] = React.useState(false)
  const [values, setValues] = React.useState(rule)
  const [search, setSearch] = React.useState('')
  const cache = useQueryClient()
  const id = React.useId()
  const plants = useQuery({ queryKey: queryKeys.plants.plants, queryFn: ({ signal }) => getPlants(signal), enabled: show })
  const varieties = useQuery({ queryKey: [...queryKeys.plants.varieties, search], queryFn: ({ signal }) => getPlantVarieties(signal, search), enabled: show })
  const mutation = useMutation({
    mutationFn: () =>
      updateWorkRule(rule.pk, {
        due_start_offset_days: values.due_start_offset_days,
        due_end_offset_days: values.due_end_offset_days,
        local_due_time: values.local_due_time,
        variety: values.variety,
        season_start: values.season_start,
        season_end: values.season_end,
        frequency: values.frequency,
        interval: values.interval,
        weekdays: values.weekdays
      }),
    onSuccess: () => {
      setShow(false)
      cache.invalidateQueries({ queryKey: queryKeys.work.all })
    }
  })
  return (
    <>
      <Button
        size="sm"
        variant="outline-secondary"
        onClick={() => {
          setValues(rule)
          mutation.reset()
          setShow(true)
        }}
      >
        Timing
      </Button>
      <Modal show={show} onHide={() => !mutation.isPending && setShow(false)} aria-labelledby={`${id}-title`}>
        <Form
          onSubmit={(event) => {
            event.preventDefault()
            mutation.mutate()
          }}
        >
          <Modal.Header closeButton>
            <Modal.Title id={`${id}-title`}>Timing: {rule.name}</Modal.Title>
          </Modal.Header>
          <Modal.Body>
            <p>
              Offsets are calendar days from the selected source window. Use negative numbers for earlier reviews. Missing source dates do not generate reminders. Times use your
              workspace timezone.
            </p>
            {mutation.isError && <Alert variant="danger">{Object.values(errorsByField(mutation.error)).join(' ') || 'Could not save timing.'}</Alert>}
            <Form.Group controlId={`${id}-start`} className="mb-3">
              <Form.Label>Start offset (days)</Form.Label>
              <Form.Control
                autoFocus
                required
                type="number"
                value={values.due_start_offset_days}
                onChange={(event) => setValues({ ...values, due_start_offset_days: Number(event.target.value) })}
              />
            </Form.Group>
            <Form.Group controlId={`${id}-end`} className="mb-3">
              <Form.Label>End offset (days)</Form.Label>
              <Form.Control
                required
                type="number"
                min={values.due_start_offset_days}
                value={values.due_end_offset_days}
                onChange={(event) => setValues({ ...values, due_end_offset_days: Number(event.target.value) })}
              />
            </Form.Group>
            <Form.Group controlId={`${id}-time`} className="mb-3">
              <Form.Label>Local due time</Form.Label>
              <Form.Control required type="time" value={values.local_due_time} onChange={(event) => setValues({ ...values, local_due_time: event.target.value })} />
            </Form.Group>
            <Form.Group controlId={`${id}-search`} className="mb-3">
              <Form.Label>Find a variety</Form.Label>
              <Form.Control value={search} onChange={(event) => setSearch(event.target.value)} />
            </Form.Group>
            <Form.Group controlId={`${id}-variety`} className="mb-3">
              <Form.Label>Applies to</Form.Label>
              <Form.Select value={values.variety ?? ''} onChange={(event) => setValues({ ...values, variety: event.target.value ? Number(event.target.value) : null })}>
                <option value="">All varieties</option>
                {values.variety && !varieties.data?.some((entry) => entry.pk === values.variety) && <option value={values.variety}>Current variety selection</option>}
                {varieties.data?.map((entry) => (
                  <option key={entry.pk} value={entry.pk}>
                    {plants.data?.find((plant) => plant.pk === entry.plant)?.name ?? 'Plant'} — {entry.name}
                  </option>
                ))}
              </Form.Select>
              {varieties.isError && <Form.Text>Could not load varieties. Your current selection is retained.</Form.Text>}
            </Form.Group>
            <Form.Group controlId={`${id}-season-start`} className="mb-3">
              <Form.Label>Season starts (MM-DD, optional)</Form.Label>
              <Form.Control placeholder="09-01" value={values.season_start} onChange={(event) => setValues({ ...values, season_start: event.target.value })} />
            </Form.Group>
            <Form.Group controlId={`${id}-season-end`} className="mb-3">
              <Form.Label>Season ends (MM-DD, optional)</Form.Label>
              <Form.Control placeholder="03-31" value={values.season_end} onChange={(event) => setValues({ ...values, season_end: event.target.value })} />
            </Form.Group>
            {rule.trigger === 'calendar' && (
              <>
                <Form.Group controlId={`${id}-frequency`} className="mb-3">
                  <Form.Label>Repeat</Form.Label>
                  <Form.Select value={values.frequency} onChange={(event) => setValues({ ...values, frequency: event.target.value as 'daily' | 'weekly' })}>
                    <option value="daily">Daily</option>
                    <option value="weekly">Weekly</option>
                  </Form.Select>
                </Form.Group>
                <Form.Group controlId={`${id}-interval`} className="mb-3">
                  <Form.Label>Every how many {values.frequency === 'weekly' ? 'weeks' : 'days'}</Form.Label>
                  <Form.Control required type="number" min={1} value={values.interval} onChange={(event) => setValues({ ...values, interval: Number(event.target.value) })} />
                </Form.Group>
                {values.frequency === 'weekly' && (
                  <fieldset>
                    <legend className="fs-6">Weekdays</legend>
                    {['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'].map((day, index) => (
                      <Form.Check
                        key={day}
                        id={`${id}-${day}`}
                        label={day}
                        checked={values.weekdays.includes(index)}
                        onChange={(event) =>
                          setValues({ ...values, weekdays: event.target.checked ? [...values.weekdays, index] : values.weekdays.filter((value) => value !== index) })
                        }
                      />
                    ))}
                  </fieldset>
                )}
              </>
            )}
          </Modal.Body>
          <Modal.Footer>
            <Button variant="secondary" onClick={() => setShow(false)} disabled={mutation.isPending}>
              Cancel
            </Button>
            <Button type="submit" disabled={mutation.isPending}>
              Save timing
            </Button>
          </Modal.Footer>
        </Form>
      </Modal>
    </>
  )
}

export { RuleTiming }
