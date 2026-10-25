"""Read the committed cost of many plants or blocks without asking once for each.

`plant_cost_breakdown` and `cohort_cost_breakdown` answer for one stock item and
carry what a screen about it wants: the batch's dispositions, the layer rows, the
sale projection. A reader pricing a whole order wants only the committed figure
for each item, and the questions the items share — the live layers, what a batch
has sold, its recount weights — can be asked once for all of them (task 141).
The figures are the ones the single-item readers return, built by the same
functions, so the two cannot drift apart.
"""

from .models import CostAllocation
from .cohort_weights import cohort_weights
from .services import COHORT_TARGETS, _cohort_cost, _committed_plant_cost
from .sources import sold_cohort_quantities


def plant_committed_costs(plants):
    """Return each plant's committed cost, read for all of them at once.

    This is the part of `plant_cost_breakdown` a cost of sale needs: the live
    layers and the figures derived from them, without the batch's dispositions,
    the layer rows or the sale projection, none of which a margin reads. The
    plants must have `batch__workspace` loaded; the layers of all of them come
    from one query.
    """
    plants = list(plants)
    layers = {}
    for row in (
        CostAllocation.objects
        .filter(specific_plant__in=plants, reversal_of__isnull=True, reversal__isnull=True)
        .order_by('pk')
    ):
        layers.setdefault(row.specific_plant_id, []).append(row)
    return {
        plant.pk: _committed_plant_cost(plant.batch, layers.get(plant.pk, []))[0]
        for plant in plants
    }


def cohort_committed_costs(cohorts):
    """Return each block's cost figures, reading each batch's shared facts once.

    The units sold out of a batch and the weights of its recounts belong to the
    batch, not to any one block of it, so a reader pricing several blocks of
    the same batch should not pay for them once per block. The cost is the
    blocks' layers in one query plus the shared reads once per distinct batch.
    """
    cohorts = list(cohorts)
    layers = {}
    for row in (
        CostAllocation.objects
        .filter(
            plant_cohort__in=cohorts,
            target_type__in=COHORT_TARGETS,
            reversal_of__isnull=True,
            reversal__isnull=True,
        )
        .order_by('pk')
    ):
        layers.setdefault(row.plant_cohort_id, []).append(row)
    sold, weights = {}, {}
    for cohort in cohorts:
        if cohort.batch_id not in sold:
            sold[cohort.batch_id] = sold_cohort_quantities(cohort.batch)
            weights[cohort.batch_id] = cohort_weights(cohort.batch)
    return {
        cohort.pk: _cohort_cost(
            cohort, layers.get(cohort.pk, []), sold[cohort.batch_id], weights[cohort.batch_id],
        )
        for cohort in cohorts
    }
