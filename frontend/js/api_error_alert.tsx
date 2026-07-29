import React from 'react'
import { Alert } from 'react-bootstrap'

import { clearApiError, getCurrentApiError, subscribeToApiErrors } from './api/errors'

const MAX_TEXT_DETAIL_LENGTH = 500

function humanizeFieldName(field: string): string {
  if (field === 'non_field_errors') {
    return 'General'
  }
  return field.replaceAll('_', ' ')
}

function formatErrorBody(body: unknown, prefix?: string): Array<string> {
  if (typeof body === 'string') {
    let message = body.trim()
    if (/^(<!doctype html|<html)/i.test(message)) {
      message = 'The server returned an HTML error page.'
    } else if (message.length > MAX_TEXT_DETAIL_LENGTH) {
      message = `${message.slice(0, MAX_TEXT_DETAIL_LENGTH)}…`
    }
    return message ? [prefix ? `${prefix}: ${message}` : message] : []
  }
  if (Array.isArray(body)) {
    return body.flatMap((value) => formatErrorBody(value, prefix))
  }
  if (typeof body === 'object' && body !== null) {
    return Object.entries(body).flatMap(([field, value]) => {
      const fieldName = humanizeFieldName(field)
      return formatErrorBody(value, prefix ? `${prefix}.${fieldName}` : fieldName)
    })
  }
  if (body === null || body === undefined) {
    return []
  }
  return [prefix ? `${prefix}: ${String(body)}` : String(body)]
}

function ApiErrorAlert() {
  const error = React.useSyncExternalStore(subscribeToApiErrors, getCurrentApiError, getCurrentApiError)

  if (error === null) {
    return null
  }

  const details = formatErrorBody(error.body)
  return (
    <Alert variant="danger" dismissible onClose={clearApiError}>
      <Alert.Heading>Request failed</Alert.Heading>
      <p className={details.length > 0 ? 'mb-2' : 'mb-0'}>{error.message}</p>
      {details.length > 0 && (
        <ul className="mb-0">
          {details.map((detail, index) => (
            <li key={`${index}-${detail}`}>{detail}</li>
          ))}
        </ul>
      )}
    </Alert>
  )
}

export { ApiErrorAlert }
