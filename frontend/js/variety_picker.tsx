import React from 'react'
import { useQuery } from '@tanstack/react-query'
import { Col, Form } from 'react-bootstrap'

import { getCropVarieties, getPlants, getPlantVarieties } from './api/plants'
import { queryKeys } from './query'

// Choose a plant variety from a list rather than typing its database number.
// The plant box only narrows the variety list; the value is the variety's id as
// a string, '' for none, which is what the report filters and URLs carry.
function VarietyPicker({ value, onChange }: { value: string; onChange: (varietyId: string) => void }) {
  const [plant, setPlant] = React.useState('')
  const plants = useQuery({ queryKey: queryKeys.plants.plants, queryFn: ({ signal }) => getPlants(signal) })
  const varieties = useQuery({
    queryKey: plant ? queryKeys.plants.cropVarieties(Number(plant)) : queryKeys.plants.varieties,
    queryFn: ({ signal }) => (plant ? getCropVarieties(Number(plant), signal) : getPlantVarieties(signal))
  })
  const listed = varieties.data ?? []
  return (
    <>
      <Col md={3}>
        <Form.Label>Plant</Form.Label>
        <Form.Select
          value={plant}
          onChange={(event) => {
            setPlant(event.target.value)
            onChange('')
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
        <Form.Select value={value} onChange={(event) => onChange(event.target.value)}>
          <option value="">Any variety</option>
          {value && !listed.some((v) => String(v.pk) === value) && <option value={value}>Variety {value}</option>}
          {listed.map((v) => (
            <option key={v.pk} value={v.pk}>
              {v.name}
            </option>
          ))}
        </Form.Select>
      </Col>
    </>
  )
}

export { VarietyPicker }
