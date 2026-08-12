type WorkTaskStatus = 'open' | 'snoozed' | 'completed' | 'skipped'
type WorkView = 'today' | 'week' | 'overdue' | 'snoozed' | 'completed'

interface WorkLink {
  role: 'target' | 'result'
  target_type: string
  object_id: number
  label: string
  url: string
  snapshot: Record<string, unknown>
  active_health_alerts: number
}

interface WorkHistory {
  pk: number
  action: string
  actor: number | null
  actor_name: string | null
  reason: string
  changes: Record<string, unknown>
  created: string
}

interface WorkTask {
  pk: number | null
  key: string
  origin: 'manual' | 'generated'
  rule: number | null
  task_type: string
  title: string
  notes: string
  priority: number
  due_start: string
  due_end: string
  status: WorkTaskStatus
  assignee: number | null
  assignee_name: string | null
  snoozed_until: string | null
  completed_at: string | null
  skipped_at: string | null
  source_snapshot: Record<string, unknown>
  recurrence: Record<string, unknown>
  links: Array<WorkLink>
  history: Array<WorkHistory>
  created: string | null
  updated: string | null
}

interface WorkRule {
  pk: number
  code: string
  name: string
  task_type: string
  trigger: string
  active: boolean
  priority: number
  due_start_offset_days: number
  due_end_offset_days: number
  local_due_time: string
  frequency: '' | 'daily' | 'weekly'
  interval: number
  weekdays: Array<number>
  season_start: string
  season_end: string
  variety: number | null
  stage: number | null
  location: number | null
  default_assignee: number | null
  notes: string
}

interface WorkAssignee {
  pk: number
  username: string
}

interface WorkFilters {
  view: WorkView
  task_type?: string
  priority?: number
  assignee?: number
  batch?: number
  location?: number
}

export { WorkAssignee, WorkFilters, WorkHistory, WorkLink, WorkRule, WorkTask, WorkTaskStatus, WorkView }
