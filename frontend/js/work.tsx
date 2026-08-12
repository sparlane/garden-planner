import React from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Badge, Button, Card, Col, Form, Nav, Row, Table } from 'react-bootstrap'
import { NavLink } from 'react-router'

import { acknowledgeWorkTask, actOnWorkTask, addWorkRule, addWorkTask, getWorkAssignees, getWorkRules, getWorkTasks, updateWorkRule } from './api/work'
import { queryKeys } from './query'
import { WorkFilters, WorkRule, WorkTask, WorkView } from './types/work'
import { formatDateTime, localDatetimeInputValue, parseLocalDatetimeInput } from './utils'

const TASK_TYPES = [
  ['germination_check', 'Germination check'],
  ['watering', 'Watering'],
  ['feeding', 'Feeding'],
  ['thinning', 'Thinning'],
  ['spacing', 'Spacing'],
  ['potting_on', 'Potting on'],
  ['hardening', 'Hardening'],
  ['ready_review', 'Ready-date review'],
  ['harvest_review', 'Harvest review'],
  ['stocktake', 'Stocktake'],
  ['order_picking', 'Order picking'],
  ['stage_review', 'Stage review'],
  ['custom', 'Custom']
]

const VIEWS: Array<[WorkView, string]> = [
  ['today', 'Today'],
  ['week', 'This week'],
  ['overdue', 'Overdue'],
  ['snoozed', 'Snoozed'],
  ['completed', 'Completed']
]

function ManualTaskForm() {
  const cache = useQueryClient()
  const [title, setTitle] = React.useState('')
  const [taskType, setTaskType] = React.useState('custom')
  const [dueStart, setDueStart] = React.useState(localDatetimeInputValue())
  const [dueEnd, setDueEnd] = React.useState(localDatetimeInputValue(new Date(Date.now() + 60 * 60 * 1000)))
  const [frequency, setFrequency] = React.useState('')
  const mutation = useMutation({
    mutationFn: addWorkTask,
    onSuccess: () => {
      setTitle('')
      cache.invalidateQueries({ queryKey: queryKeys.work.all })
    }
  })
  const submit = (event: React.FormEvent) => {
    event.preventDefault()
    const start = parseLocalDatetimeInput(dueStart)
    const end = parseLocalDatetimeInput(dueEnd)
    if (!start || !end) return
    mutation.mutate({
      title,
      task_type: taskType,
      due_start: start.toISOString(),
      due_end: end.toISOString(),
      recurrence: frequency ? { frequency, interval: 1, weekdays: frequency === 'weekly' ? [new Date(start).getDay() === 0 ? 6 : new Date(start).getDay() - 1] : [] } : {}
    })
  }
  return (
    <Card className="mb-3">
      <Card.Header>Schedule manual work</Card.Header>
      <Card.Body>
        <Form onSubmit={submit}>
          <Row className="g-2">
            <Col md={4}>
              <Form.Control required placeholder="Work to do" value={title} onChange={(event) => setTitle(event.target.value)} />
            </Col>
            <Col md={2}>
              <Form.Select value={taskType} onChange={(event) => setTaskType(event.target.value)}>
                {TASK_TYPES.map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </Form.Select>
            </Col>
            <Col md={2}>
              <Form.Control required type="datetime-local" value={dueStart} onChange={(event) => setDueStart(event.target.value)} />
            </Col>
            <Col md={2}>
              <Form.Control required type="datetime-local" value={dueEnd} onChange={(event) => setDueEnd(event.target.value)} />
            </Col>
            <Col md={1}>
              <Form.Select aria-label="Recurrence" value={frequency} onChange={(event) => setFrequency(event.target.value)}>
                <option value="">Once</option>
                <option value="daily">Daily</option>
                <option value="weekly">Weekly</option>
              </Form.Select>
            </Col>
            <Col md={1}>
              <Button type="submit" disabled={mutation.isPending}>
                Add
              </Button>
            </Col>
          </Row>
        </Form>
      </Card.Body>
    </Card>
  )
}

function TaskActions({ task }: { task: WorkTask }) {
  const cache = useQueryClient()
  const assignees = useQuery({ queryKey: queryKeys.work.assignees, queryFn: ({ signal }) => getWorkAssignees(signal) })
  const mutation = useMutation({
    mutationFn: async (data: Record<string, unknown>) => {
      const acknowledged = task.pk === null ? await acknowledgeWorkTask(task.key) : task
      return actOnWorkTask(acknowledged.pk as number, { ...data, idempotency_key: crypto.randomUUID() })
    },
    onSuccess: () => cache.invalidateQueries({ queryKey: queryKeys.work.all })
  })
  const reasoned = (action: 'skip' | 'reopen') => {
    const reason = globalThis.prompt(`Reason to ${action} this task:`)
    if (reason) mutation.mutate({ action, reason })
  }
  if (task.status === 'completed' || task.status === 'skipped') {
    return (
      <Button size="sm" variant="outline-secondary" onClick={() => reasoned('reopen')}>
        Reopen
      </Button>
    )
  }
  return (
    <div className="d-flex flex-wrap gap-1">
      <Button size="sm" onClick={() => mutation.mutate({ action: 'claim' })}>
        Claim
      </Button>
      <Form.Select
        size="sm"
        aria-label={`Assign ${task.title}`}
        value={task.assignee ?? ''}
        onChange={(event) => mutation.mutate({ action: 'assign', assignee: event.target.value ? Number(event.target.value) : null })}
      >
        <option value="">Unassigned</option>
        {(assignees.data ?? []).map((user) => (
          <option key={user.pk} value={user.pk}>
            {user.username}
          </option>
        ))}
      </Form.Select>
      <Button size="sm" variant="outline-secondary" onClick={() => mutation.mutate({ action: 'snooze', until: new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString() })}>
        Snooze 1 day
      </Button>
      <Button size="sm" variant="success" onClick={() => mutation.mutate({ action: 'complete' })}>
        Complete
      </Button>
      <Button size="sm" variant="outline-danger" onClick={() => reasoned('skip')}>
        Skip
      </Button>
    </div>
  )
}

function TaskTable({ tasks }: { tasks: Array<WorkTask> }) {
  return (
    <Table responsive hover>
      <thead>
        <tr>
          <th>Due</th>
          <th>Work</th>
          <th>Targets</th>
          <th>Assignee</th>
          <th>Priority</th>
          <th>Actions</th>
        </tr>
      </thead>
      <tbody>
        {tasks.map((task) => (
          <tr key={task.key}>
            <td>
              {formatDateTime(task.due_start)}
              <div className="small text-muted">to {formatDateTime(task.due_end)}</div>
            </td>
            <td>
              <strong>{task.title}</strong>
              <div>
                <Badge bg={task.origin === 'generated' ? 'info' : 'secondary'}>{task.origin}</Badge> {task.task_type.replaceAll('_', ' ')}
              </div>
              {task.notes && <div>{task.notes}</div>}
              {task.history.length > 0 && (
                <details className="mt-1">
                  <summary>History ({task.history.length})</summary>
                  {task.history.map((entry) => (
                    <div className="small" key={entry.pk}>
                      {formatDateTime(entry.created)} · {entry.action} · {entry.actor_name ?? 'system'}
                      {entry.reason ? ` — ${entry.reason}` : ''}
                    </div>
                  ))}
                </details>
              )}
            </td>
            <td>
              {task.links
                .filter((link) => link.role === 'target')
                .map((link) => (
                  <div key={`${link.target_type}:${link.object_id}`}>{link.url ? <NavLink to={link.url}>{link.label}</NavLink> : link.label}</div>
                ))}
            </td>
            <td>{task.assignee_name ?? 'Unassigned'}</td>
            <td>{task.priority}</td>
            <td>
              <TaskActions task={task} />
            </td>
          </tr>
        ))}
        {tasks.length === 0 && (
          <tr>
            <td colSpan={6} className="text-muted">
              No work in this view.
            </td>
          </tr>
        )}
      </tbody>
    </Table>
  )
}

function RuleEditor({ rules }: { rules: Array<WorkRule> }) {
  const cache = useQueryClient()
  const [name, setName] = React.useState('')
  const [taskType, setTaskType] = React.useState('watering')
  const [frequency, setFrequency] = React.useState<'daily' | 'weekly'>('daily')
  const mutation = useMutation({
    mutationFn: ({ pk, values }: { pk?: number; values: object }) => (pk ? updateWorkRule(pk, values) : addWorkRule(values)),
    onSuccess: () => cache.invalidateQueries({ queryKey: queryKeys.work.rules })
  })
  const add = () =>
    mutation.mutate({
      values: {
        code: `care-${Date.now()}`,
        name,
        task_type: taskType,
        trigger: 'calendar',
        frequency,
        interval: 1,
        weekdays: frequency === 'weekly' ? [0] : [],
        priority: 20,
        due_start_offset_days: 0,
        due_end_offset_days: 0,
        local_due_time: '09:00'
      }
    })
  return (
    <Card className="mt-4">
      <Card.Header>Automation rules</Card.Header>
      <Card.Body>
        <Table size="sm">
          <thead>
            <tr>
              <th>Rule</th>
              <th>Trigger</th>
              <th>Recurrence</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rules.map((rule) => (
              <tr key={rule.pk}>
                <td>{rule.name}</td>
                <td>{rule.trigger.replaceAll('_', ' ')}</td>
                <td>{rule.frequency || 'From source date'}</td>
                <td>
                  <Button size="sm" variant="outline-secondary" onClick={() => mutation.mutate({ pk: rule.pk, values: { active: !rule.active } })}>
                    {rule.active ? 'Disable' : 'Enable'}
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
        <Row className="g-2">
          <Col>
            <Form.Control placeholder="New care rule" value={name} onChange={(event) => setName(event.target.value)} />
          </Col>
          <Col>
            <Form.Select value={taskType} onChange={(event) => setTaskType(event.target.value)}>
              {TASK_TYPES.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </Form.Select>
          </Col>
          <Col>
            <Form.Select value={frequency} onChange={(event) => setFrequency(event.target.value as 'daily' | 'weekly')}>
              <option value="daily">Daily</option>
              <option value="weekly">Weekly on Monday</option>
            </Form.Select>
          </Col>
          <Col xs="auto">
            <Button disabled={!name} onClick={add}>
              Add rule
            </Button>
          </Col>
        </Row>
      </Card.Body>
    </Card>
  )
}

function WorkQueueView() {
  const [view, setView] = React.useState<WorkView>('today')
  const [taskType, setTaskType] = React.useState('')
  const [priority, setPriority] = React.useState('')
  const [assignee, setAssignee] = React.useState('')
  const [batch, setBatch] = React.useState('')
  const [location, setLocation] = React.useState('')
  const filters: WorkFilters = {
    view,
    task_type: taskType || undefined,
    priority: priority ? Number(priority) : undefined,
    assignee: assignee ? Number(assignee) : undefined,
    batch: batch ? Number(batch) : undefined,
    location: location ? Number(location) : undefined
  }
  const tasks = useQuery({ queryKey: queryKeys.work.tasks(filters), queryFn: ({ signal }) => getWorkTasks(filters, signal) })
  const rules = useQuery({ queryKey: queryKeys.work.rules, queryFn: ({ signal }) => getWorkRules(signal) })
  const assignees = useQuery({ queryKey: queryKeys.work.assignees, queryFn: ({ signal }) => getWorkAssignees(signal) })
  return (
    <main className="container-fluid py-3">
      <h1>Nursery work</h1>
      <p>Generated and manually scheduled work stays here until it is completed or deliberately skipped.</p>
      <ManualTaskForm />
      <Nav variant="tabs" activeKey={view} onSelect={(key) => key && setView(key as WorkView)}>
        {VIEWS.map(([value, label]) => (
          <Nav.Item key={value}>
            <Nav.Link eventKey={value}>{label}</Nav.Link>
          </Nav.Item>
        ))}
      </Nav>
      <Row className="g-2 py-3">
        <Col md={3}>
          <Form.Select aria-label="Task type" value={taskType} onChange={(event) => setTaskType(event.target.value)}>
            <option value="">All task types</option>
            {TASK_TYPES.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </Form.Select>
        </Col>
        <Col md={2}>
          <Form.Control aria-label="Batch ID" type="number" min={1} placeholder="Batch ID" value={batch} onChange={(event) => setBatch(event.target.value)} />
        </Col>
        <Col md={2}>
          <Form.Control aria-label="Location ID" type="number" min={1} placeholder="Location ID" value={location} onChange={(event) => setLocation(event.target.value)} />
        </Col>
        <Col md={2}>
          <Form.Control aria-label="Priority" type="number" placeholder="Priority" value={priority} onChange={(event) => setPriority(event.target.value)} />
        </Col>
        <Col md={3}>
          <Form.Select aria-label="Assignee" value={assignee} onChange={(event) => setAssignee(event.target.value)}>
            <option value="">All assignees</option>
            {(assignees.data ?? []).map((user) => (
              <option key={user.pk} value={user.pk}>
                {user.username}
              </option>
            ))}
          </Form.Select>
        </Col>
      </Row>
      <TaskTable tasks={tasks.data ?? []} />
      <RuleEditor rules={rules.data ?? []} />
    </main>
  )
}

export { WorkQueueView }
