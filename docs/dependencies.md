# Application dependencies

Dependencies are defined between modules, not entire Django apps. An app's
models, read projections, commands, and REST endpoints have different roles.
For example, `inventory.models` reads `locations.models`, while
`locations.occupancy` reads inventory and cultivation models. That is not a
module import cycle: neither model module imports the occupancy projection.

## Direction

| Caller | Intended dependencies |
| --- | --- |
| `common` utilities | Python and framework primitives only; no application modules. Shared query parsers live in `common.rest_query`, the catalog retirement rule in `common.retirement`, and the catalog merge rule in `common.merging`, which builds on it. |
| Workspace, location, catalog, and stock models | Foundational models and pure validation helpers. No command services, REST endpoints, or router registration. |
| Cultivation, sales, application, and accounting models | Other models needed to describe their facts. Use string relations where Django requires delayed model resolution. |
| Read projections (`locations.occupancy`, availability, costing sources, reporting, work projections) | Models and other read projections. Reading another app's facts does not require importing its commands or endpoints. |
| Domain commands (`inventory.ledger`, stocktakes, cultivation, health, sales) | Models, projections, and the domain services they coordinate. Inventory stocktakes are a coordinator across stock kinds, not a foundational model layer. |
| Costing services | Cultivation and input facts, including sowing consumption, germination balances, and tray generations. Posting those facts has the documented callbacks below. |
| REST endpoints and routers | Payload adapters, serializers, and domain services. Routers compose endpoint modules; domain services never import routers or viewsets. |
| Tests | Any production layer needed by their scenario. Production modules never import test helpers. |

`plantings.movement` owns plant movement and location-history validation. It is
called by plant endpoints, stocktakes, health operations, sales returns, and bulk
work. `plantings.movement_rest` holds their shared move serializer.
`applications.requests` converts validated application payloads to request
objects for both application endpoints and bulk repotting. Both extracted
services retain the existing DRF validation-error contract; the extraction does
not change transaction boundaries or error responses.

Inventory's catalog router composes ledger, serialized-unit, and nursery
stocktake endpoints. Ledger serializers do not import those consumers back.

## Deferred import audit (task 104)

The implementation baseline contained **133** `import-outside-toplevel`
suppressions, including tests (the original task described an older count of
81). The audit removed **123**, leaving **10** with a reason at each site.

The removed sites were ordinary model reads, framework/standard-library
utilities, command calls without a return dependency, and test fixtures. These
are now module imports. This includes ten of the eleven imports in
`seedtrays.generations`, all occupancy reads, inventory reservation/usage reads,
and the health/work, tax/reporting, and sales/costing projections. Module-level
imports make their actual dependencies visible without suggesting that every
cross-app read is a cycle.

Three routing imports disappeared through composition changes: two imports in
`inventory.ledger_rest.register_ledger_routes` and the bulk route import in
`plantings.rest`. The other removed imports were hoisted or merged with an
existing import. Existing behavioral assertions and fixtures were preserved.

| Remaining sites | Reason |
| --- | --- |
| `plantings.batches.finalize_batch_output` | Finalization posts costs; costing reads batch/cultivation facts. |
| `plantings.sowing._reallocate` | Sowing posts costs; costing reads sowing consumption. |
| `plantings.germination.close_germination` and `reopen_germination` | Germination changes costs; costing reads germination balances. |
| `seedtrays.generations._reallocate` | Tray correction reallocates costs; costing reads tray generations. |
| `ready()` in bookkeeping, health, labels, plantings, and work app configs | Register signals only after Django populates the model registry. These need no cycle suppression. |

The five costing imports carry **import-line** `cyclic-import` disables.
Pylint excludes that module-to-module edge from its cycle graph, not the entire
module. A second import from the same source module to the same target is still
the same graph edge; a different target remains checked. The regression test
uses a temporary three-module package to verify that an approved edge does not
hide a new cycle beside it, using the repository's actual lint configuration.

## Adding a dependency

Use a normal module import first. If it creates a cycle, check whether a shared
primitive, serializer, or request adapter belongs below both callers, or whether
router composition belongs above them. Do not defer an import merely because
its app is also a caller elsewhere.

If the operation really is a callback into a service that reads the caller's
facts, keep it function-scoped and explain that return dependency immediately
above the import. Suppress `cyclic-import` on that import only and record the
decision here. Never disable the check globally or for a whole module.

Run `./check-code.sh` and `./test-venv.sh` (the latter invokes `manage.py test`).
The dependency regression tests also check fresh-process service loading, so
test discovery cannot conceal an accidental service-to-REST dependency.
