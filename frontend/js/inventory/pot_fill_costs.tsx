import React from 'react'
import { Badge, Table } from 'react-bootstrap'
import { Link } from 'react-router'

import { PotCostSummary, PotFillPendingCost } from '../api/container_fills'
import { formatMoney, sumMoney } from '../utils'

const BLOCKED_LABELS = { quarantined: 'Quarantined', not_sellable: 'Not ready to sell' }

// Two currencies are listed side by side, never added, and one unknown input
// makes the whole figure unknown rather than a smaller number.
function formatSummary(summary: PotCostSummary): string {
  if (summary.unknown_cost) return 'Unknown'
  if (summary.total !== null && summary.currency_code !== null) return formatMoney(summary.total, summary.currency_code)
  if (summary.totals.length === 0) return 'nothing posted yet'
  return `${summary.totals.map((row) => formatMoney(row.amount, row.currency_code)).join(' + ')} (not combined)`
}

export function PotFillCosts({ costs }: { costs: PotFillPendingCost }) {
  if (costs.pot_count === 0) return null
  return (
    <>
      <h3 className="h6 mt-3">Planted pots if dispatched today</h3>
      <p className="small text-muted">
        What each planted pot would cost if its plants went out with it now: what they have cost to raise, the mix they stand in, and the container once. Media in unplanted pots
        stays with the fill and is not counted here, unlike “Held in the fill”.
      </p>
      <Table size="sm" responsive>
        <thead>
          <tr>
            <th>Pot</th>
            <th>Plants</th>
            <th>Mix held</th>
            <th>Container</th>
            <th>Total</th>
          </tr>
        </thead>
        <tbody>
          {costs.pots.map((pot) => {
            const media = pot.plants.map((row) => row.pending[0])
            const mediaKnown = media.every((entry) => entry.amount !== null)
            return (
              <tr key={pot.container_unit ?? `placement:${pot.placement}`}>
                <td>{pot.container_unit !== null ? `Numbered pot #${pot.container_unit}` : 'Counted pot'}</td>
                <td>
                  {pot.plants.map((row) => (
                    <span className="me-2" key={row.plant}>
                      <Link to={`/plantings/plants/${row.plant}`}>Plant #{row.plant}</Link> <span className="small text-muted">(committed: {formatSummary(row.committed)})</span>
                      {row.dispatch_blocked && (
                        <Badge bg="warning" text="dark" className="ms-1">
                          {BLOCKED_LABELS[row.dispatch_blocked]}
                        </Badge>
                      )}
                    </span>
                  ))}
                </td>
                <td>{mediaKnown ? formatMoney(sumMoney(media.map((entry) => entry.amount)), media[0].currency_code) : 'Unknown'}</td>
                <td>{formatMoney(pot.container_cost.amount, pot.container_cost.currency_code, 'Unknown')}</td>
                <td>{formatSummary(pot)}</td>
              </tr>
            )
          })}
        </tbody>
        <tfoot>
          <tr>
            <th scope="row" colSpan={4}>
              {costs.pot_count === 1 ? 'This pot' : `All ${costs.pot_count} planted pots`}
            </th>
            <th>{formatSummary(costs)}</th>
          </tr>
        </tfoot>
      </Table>
      {costs.pots.some((pot) => !pot.dispatchable) && (
        <p className="small">Flagged plants still have a cost, but cannot be dispatched until they are ready and out of quarantine.</p>
      )}
    </>
  )
}
