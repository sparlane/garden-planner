import React from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Form } from 'react-bootstrap'

import { cleanPotFill, PotFillContents, reopenPotFill } from '../api/container_fills'
import { getLocations } from '../api/locations'
import { queryKeys } from '../query'
import { MediaDispositionChoice } from '../types/seedtrays'
import { errorsByField } from '../utils'

// The parent keys this form by the contents digest: refreshed media requires a
// fresh confirmation, never a silent substitution in an operator's request.
export function PotFillClean({ pk, contents }: { pk: number; contents: PotFillContents }) {
  const cache = useQueryClient()
  const [reason, setReason] = React.useState('')
  const [choices, setChoices] = React.useState<Record<number, { disposition: MediaDispositionChoice | ''; destination: string }>>({})
  const closed = contents.status === 'closed'
  const locations = useQuery({
    queryKey: queryKeys.locations.list('active'),
    queryFn: ({ signal }) => getLocations(signal, true),
    enabled: !closed && contents.media.length > 0
  })
  const mutation = useMutation({
    mutationFn: () =>
      closed
        ? reopenPotFill(pk, reason)
        : cleanPotFill(pk, {
            reason,
            digest: contents.digest,
            media: contents.media.map((media) => ({
              lot: media.lot,
              quantity: media.base_quantity,
              disposition: choices[media.lot].disposition as MediaDispositionChoice,
              reason,
              destination: choices[media.lot].disposition === 'reclaimed' ? Number(choices[media.lot].destination) : null
            }))
          }),
    onSuccess: async () => {
      await Promise.all(
        [queryKeys.containerFills.all, queryKeys.inventory.all, queryKeys.reports.all, queryKeys.costing.all, queryKeys.locations.all].map((queryKey) =>
          cache.invalidateQueries({ queryKey })
        )
      )
    }
  })
  const incomplete = contents.media.some((media) => {
    const choice = choices[media.lot]
    return !choice?.disposition || (choice.disposition === 'reclaimed' && !choice.destination)
  })
  if (!closed && contents.plants.length > 0) return <p>Move all plants out of this fill before cleaning it.</p>
  return (
    <Form
      className="mt-3"
      onSubmit={(event) => {
        event.preventDefault()
        mutation.mutate()
      }}
    >
      <h3 className="h6">{closed ? 'Correct a mistaken clean' : 'Clean this fill'}</h3>
      <p>
        {closed
          ? 'Reopen this fill only to correct a mistaken clean. This restores its pot claim and takes any reclaimed media back out of stock. The original clean remains in the history.'
          : 'Confirm what happened to each remaining media lot. Cleaning releases the empty pots for reuse; media already taken by departed plants keeps its cost.'}
      </p>
      <fieldset disabled={mutation.isPending}>
        {!closed &&
          contents.media.map((media) => {
            const choice = choices[media.lot] ?? { disposition: '', destination: '' }
            return (
              <div key={media.lot} className="mb-3">
                <Form.Group controlId={`fill-${pk}-media-${media.lot}`}>
                  <Form.Label>
                    Lot #{media.lot}: {media.base_quantity} {media.base_unit}
                  </Form.Label>
                  <Form.Select
                    required
                    value={choice.disposition}
                    onChange={(event) => setChoices({ ...choices, [media.lot]: { ...choice, disposition: event.target.value as MediaDispositionChoice } })}
                  >
                    <option value="">Choose what happened to this media</option>
                    <option value="waste">Discarded as waste</option>
                    <option value="reclaimed">Reclaimed into stock</option>
                  </Form.Select>
                </Form.Group>
                {choice.disposition === 'reclaimed' && (
                  <Form.Group controlId={`fill-${pk}-destination-${media.lot}`} className="mt-2">
                    <Form.Label>Reclaimed media location</Form.Label>
                    <Form.Select required value={choice.destination} onChange={(event) => setChoices({ ...choices, [media.lot]: { ...choice, destination: event.target.value } })}>
                      <option value="">Choose a location</option>
                      {(locations.data ?? []).map((location) => (
                        <option key={location.pk} value={location.pk}>
                          {location.full_name}
                        </option>
                      ))}
                    </Form.Select>
                    {locations.isPending && <p>Loading locations…</p>}
                    {locations.isError && <Alert variant="danger">Could not load reclaim locations.</Alert>}
                  </Form.Group>
                )}
              </div>
            )
          })}
        <Form.Group controlId={`fill-${pk}-clean-reason`} className="mb-2">
          <Form.Label>{closed ? 'Correction reason' : 'Clean reason'}</Form.Label>
          <Form.Control required as="textarea" value={reason} onChange={(event) => setReason(event.target.value)} />
        </Form.Group>
        <Button type="submit" disabled={!reason.trim() || (!closed && incomplete) || mutation.isPending}>
          {mutation.isPending ? 'Saving…' : closed ? 'Correct clean and reopen fill' : 'Confirm clean'}
        </Button>
      </fieldset>
      {mutation.isError && (
        <Alert variant="danger" className="mt-2">
          {Object.values(errorsByField(mutation.error)).join(' ') || mutation.error.message}
          <Button variant="link" onClick={() => cache.invalidateQueries({ queryKey: queryKeys.containerFills.contents(pk) })}>
            Refresh contents before trying again
          </Button>
        </Alert>
      )}
    </Form>
  )
}
