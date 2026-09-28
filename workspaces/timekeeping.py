"""Turn a day a workspace recorded into the instant it began.

A purchase, an expense and a garden planting are dated by the day, not by the
moment, so anything that has to place one on a timeline has to pick an instant
for it. The start of that day in the workspace's own zone is the choice
`bookkeeping.services` already makes for a balance date, which is what keeps a
cost incurred on 31 March inside the year that ended on it.

It lives here rather than beside its first caller because four apps now need
it — costing dates a layer with it, purchasing and the Garden quick-add date
the runs they prompt — and every one of them already depends on workspaces.
"""

from datetime import datetime, time
from zoneinfo import ZoneInfo


def recorded_day(workspace, on_date):
    """Return the instant `on_date` began where this workspace keeps time."""
    return datetime.combine(on_date, time.min, ZoneInfo(workspace.timezone))
