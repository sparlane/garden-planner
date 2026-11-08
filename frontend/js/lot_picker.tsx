import React from 'react'
import { useQuery } from '@tanstack/react-query'
import { Col, Form } from 'react-bootstrap'

import { getPlants, getCropVarieties, getPlantVarieties } from './api/plants'
import { getReport } from './api/reports'
import { queryKeys } from './query'
import { formatDate } from './utils'

interface LotChoiceRow {
  lot_id: number
  lot_identifier: string
  item_name: string
  plant_name: string | null
  variety_name: string | null
  supplier_lot_reference: string
  received_on: string | null
}

function lotLabel(lot: LotChoiceRow): string {
  const what = lot.variety_name ? `${lot.plant_name} – ${lot.variety_name}` : lot.item_name
  const detail = [lot.supplier_lot_reference, lot.received_on ? `received ${formatDate(lot.received_on)}` : ''].filter(Boolean).join(', ')
  return `${what} (${lot.lot_identifier}${detail ? `, ${detail}` : ''})`
}

// Choose a stock lot from a list rather than typing its database number. The
// plant and variety boxes and the text search only narrow the list; the value
// is the lot's id as a string, '' for none, which is what the report filters
// and URLs already carry.
function LotPicker({ label = 'Lot', value, onChange }: { label?: string; value: string; onChange: (lotId: string) => void }) {
  const [plant, setPlant] = React.useState('')
  const [variety, setVariety] = React.useState('')
  const [search, setSearch] = React.useState('')
  const plants = useQuery({ queryKey: queryKeys.plants.plants, queryFn: ({ signal }) => getPlants(signal) })
  const varieties = useQuery({
    queryKey: plant ? queryKeys.plants.cropVarieties(Number(plant)) : queryKeys.plants.varieties,
    queryFn: ({ signal }) => (plant ? getCropVarieties(Number(plant), signal) : getPlantVarieties(signal))
  })
  const filters = new URLSearchParams({ page_size: '200' })
  if (plant) filters.set('plant', plant)
  if (variety) filters.set('variety', variety)
  if (search.trim()) filters.set('search', search.trim())
  const lots = useQuery({
    queryKey: queryKeys.reports.lotChoices(filters.toString()),
    queryFn: ({ signal }) => getReport<LotChoiceRow>('lot-choices', filters, signal)
  })
  const rows = lots.data?.results ?? []
  const shown = lots.data && lots.data.count > rows.length
  return (
    <>
      <Col md={3}>
        <Form.Label>Plant</Form.Label>
        <Form.Select
          value={plant}
          onChange={(event) => {
            setPlant(event.target.value)
            setVariety('')
          }}
        >
          <option value="">Any plant</option>
          {plants.data?.map((p) => (
            <option key={p.pk} value={p.pk}>
              {p.name}
            </option>
          ))}
        </Form.Select>
      </Col>
      <Col md={3}>
        <Form.Label>Variety</Form.Label>
        <Form.Select value={variety} onChange={(event) => setVariety(event.target.value)}>
          <option value="">Any variety</option>
          {varieties.data?.map((v) => (
            <option key={v.pk} value={v.pk}>
              {v.name}
            </option>
          ))}
        </Form.Select>
      </Col>
      <Col md={3}>
        <Form.Label>Search lots</Form.Label>
        <Form.Control value={search} placeholder="Lot, supplier reference or name" onChange={(event) => setSearch(event.target.value)} />
      </Col>
      <Col md={3}>
        <Form.Label>{label}</Form.Label>
        <Form.Select aria-label={label} value={value} onChange={(event) => onChange(event.target.value)}>
          <option value="">{lots.isPending ? 'Loading…' : `Choose a lot (${lots.data?.count ?? 0})`}</option>
          {value && !rows.some((row) => String(row.lot_id) === value) && <option value={value}>Lot {value}</option>}
          {rows.map((row) => (
            <option key={row.lot_id} value={row.lot_id}>
              {lotLabel(row)}
            </option>
          ))}
        </Form.Select>
        {shown && <Form.Text muted>Showing the first {rows.length}; narrow the list to see the rest.</Form.Text>}
      </Col>
    </>
  )
}

export { LotPicker }
export type { LotChoiceRow }
