# CRT Cost Dashboard And Database Architecture

This document defines the first implementation direction for turning the CRT Cost agent into a data-aware dashboard agent. The goal is to keep the clean source data governed and read-only while letting the agent create sandboxed dashboard/chart specifications from user prompts.

## Definition: "100% Flexible"

In this project, "100% flexible" should not mean arbitrary code execution or arbitrary SQL against production data. It means:

1. Users can describe a dashboard, chart, filter, metric, or calculation in business language.
2. The agent maps that request to a governed data catalog: approved fields, approved metrics, known formulas, and allowed chart types.
3. If the request is underspecified, the agent asks focused clarification questions instead of guessing.
4. The agent can create a reusable dashboard specification that the UI can render, pin, revisit, and later execute against approved data tools.
5. The source database remains untouched. Any user-created filters, formulas, derived columns, dashboards, or experiments live in a sandbox layer linked to a session/workspace.

That is the practical version of flexibility: prompt-driven dashboard design inside a governed schema and tool boundary.

## Current Implemented Scaffold

This branch adds the first version of that scaffold.

### Backend

- `app/dashboard_store.py`
  - Adds a SQLite-backed `DashboardStore`.
  - Stores pinned dashboard/chart specs in `dashboard_specs`.
  - Each record has workspace, session, title, status, pinned flag, and JSON spec.

- `app/tools.py`
  - Adds `crt_cost_dataset_catalog`.
  - Adds `crt_cost_dashboard_spec`.
  - The catalog tool exposes a governed starter schema for CRT Cost deal-level dashboarding.
  - The spec tool validates requested metrics/groupings/filters against the catalog and saves a sandboxed dashboard spec.

- `app/orchestrator.py`
  - Wires `DashboardStore` into the runtime.
  - Adds system instructions telling the model to use the CRT Cost dashboard tools for dashboard requests.
  - Adds progress messages for dashboard planning tool calls.

- `app/runtime_hooks.py`
  - Adds a dashboard hook.
  - When `crt_cost_dataset_catalog` or `crt_cost_dashboard_spec` runs, the backend emits a UI event to open the dashboard drawer.

- `app/api_server.py`
  - Adds dashboard counts to `/health`.
  - Adds `GET /agent/dashboards` for the UI to list saved specs.

### Frontend

- `web/index.html`
  - Adds a `Dashboards` sidebar button.
  - Adds a reusable dashboard drawer view.
  - Adds drawer controls for `Maximize`, `Side View`, and `Focus Chat`.

- `web/app.js`
  - Extends `DRAWER_VIEWS` with `dashboard`.
  - Adds dashboard state, rendering, refresh, mock data, and UI-event handling.
  - Mock mode can now trigger dashboard drawer behavior when the user asks for chart/dashboard/aggregation style requests.

- `web/styles.css`
  - Adds dashboard cards, chips, previews, and source/sandbox boundary styling.
  - Adds full-drawer mode where the drawer can occupy the full screen and chat shrinks to a small bottom-right panel.

## Current CRT Cost Catalog Seed

The starter catalog is intentionally small and business-readable. It is not yet connected to real CRT Cost source data.

Dataset:

- `crt_cost_deal_level`
- Grain: one row per deal

Starter fields:

- `deal_id`
- `deal_name`
- `deal_type`
- `settle_year`
- `payoff_date`
- `upb`
- `crt_cost`
- `crt_cost_bps`
- `partial_year_factor`

Starter metrics:

- `sum_crt_cost`
- `sum_upb`
- `crt_cost_bps`
- `normalized_partial_year_crt_cost`

Important status:

- `crt_cost_bps` is marked as requiring business validation.
- `normalized_partial_year_crt_cost` is marked as a knowledge gap until the approved formula is documented.

## Runtime Example: User Requests A Dashboard

User:

> Create a bar chart of CRT Cost by settle year for STACR deals.

Expected runtime path:

1. The model sees dashboard-specific instructions in the system prompt.
2. The model calls `crt_cost_dataset_catalog`.
3. The model uses the catalog to identify:
   - metric: `sum_crt_cost`
   - group: `settle_year`
   - filter: `deal_type equals STACR`
   - chart type: `bar`
4. The model calls `crt_cost_dashboard_spec`.
5. The backend stores the spec in `dashboard_specs`.
6. The runtime hook emits an `open_drawer` UI event for the dashboard drawer.
7. The UI opens the dashboard drawer and lists the pinned dashboard spec.
8. The agent explains that this is a dashboard specification, not a real executed aggregation yet.

Current limitation:

- No actual source rows are queried yet.
- No aggregation result is calculated yet.
- The preview is a structural preview only.

## Runtime Example: User Request Is Underspecified

User:

> Make me a chart for expensive deals.

Expected runtime path:

1. The model calls `crt_cost_dataset_catalog`.
2. The model calls `crt_cost_dashboard_spec` with known details and missing assumptions.
3. The tool saves the spec with status `needs_clarification`.
4. The tool returns clarification questions such as:
   - Which approved CRT Cost metric should this dashboard use?
   - Which grouping or filter should define the business view?
5. The agent asks the user to clarify instead of inventing a threshold.

## Database Design Direction

The database should be split into clean source and sandbox layers.

### Clean Source Layer

Purpose:

- Hold approved CRT Cost source rows.
- Preserve data lineage.
- Avoid accidental mutation through chat.

Rules:

- Read-only to the agent.
- No chat-created transformations overwrite this layer.
- Data refresh is done by approved ETL or a controlled admin process.

Future tables/files:

- `source_datasets`
- `source_columns`
- `source_snapshots`
- `source_lineage`
- source files in a governed input folder

### Catalog Layer

Purpose:

- Teach the agent what each field means.
- Define metrics, formulas, allowed operators, and chart-safe fields.
- Provide business definitions and source references.

Future tables/files:

- `dataset_registry`
- `field_catalog`
- `metric_catalog`
- `formula_catalog`
- `dashboard_template_catalog`

The catalog should be built from:

- uploaded user guides,
- methodology documents,
- data dictionaries,
- model review/use documents,
- approved developer notes,
- daily corrections promoted into wiki/glossary pages.

### Sandbox Layer

Purpose:

- Let users and the agent create experiments without touching clean source.
- Keep dashboard specs, derived-column proposals, filter sets, formula drafts, and generated charts tied to session/workspace history.

Current table:

- `dashboard_specs`

Future tables:

- `sandbox_transforms`
- `sandbox_formula_runs`
- `sandbox_aggregation_results`
- `pinned_dashboard_views`
- `chart_render_specs`

## Tool Roadmap

Implemented now:

- `crt_cost_dataset_catalog`
  - Returns the governed starter catalog.
  - Use before dashboard/chart planning.

- `crt_cost_dashboard_spec`
  - Creates and pins a dashboard/chart spec.
  - Does not execute source data.
  - Does not mutate source data.

Next deterministic tools:

1. `crt_cost_profile_dataset`
   - Reads a safe sample or approved source snapshot.
   - Returns row count, column names, missingness, min/max, distinct counts.

2. `crt_cost_aggregate`
   - Executes approved aggregations only.
   - Inputs: metrics, group_by, filters.
   - Output: table rows plus trace metadata.

3. `crt_cost_validate_formula`
   - Tests a formula against sample rows.
   - Returns examples, warnings, divide-by-zero handling, and data type checks.

4. `crt_cost_create_derived_column`
   - Creates a sandbox-only derived-column proposal.
   - Requires formula source or explicit user approval.

5. `crt_cost_render_dashboard`
   - Converts a saved spec plus aggregation result into a renderable dashboard payload.

6. `crt_cost_export_dashboard`
   - Exports table/chart output to CSV/XLSX/HTML after the output is approved.

## UI Direction

The drawer should be a reusable right-side workspace, not only a knowledge base panel.

Current drawer views:

- `knowledge`
- `dashboard`
- `empty`

Future drawer views:

- `files`
- `planner`
- `schedule`
- `trace`
- `data_profile`
- `formula_audit`

Interaction model:

1. Chat remains the main instruction surface.
2. Tool calls can open the drawer through backend UI events.
3. The drawer can show the concrete artifact produced by the tool.
4. The drawer can be maximized.
5. When maximized, chat shrinks to a small bottom-right panel.
6. `Focus Chat` returns the user to the chat-first view.

For dashboarding, the drawer should eventually show:

- dashboard cards,
- pinned specs,
- filter controls,
- chart previews,
- data table previews,
- formula warnings,
- source/sandbox boundary,
- export controls.

## How The Tool List Is Presented To FredAI

Tools are passed as OpenAI-compatible function schemas. For the dashboard tools, the model sees names and descriptions like:

```text
crt_cost_dataset_catalog
Return the governed CRT Cost dashboard catalog: deal-level fields, approved starter metrics, filter operators, chart types, and clean-source/sandbox boundaries.

crt_cost_dashboard_spec
Create and pin a CRT Cost dashboard/chart specification from the user's request. This records metrics, grouping, filters, assumptions, and needed follow-up questions without modifying source data.
```

That wording is intentional. It tells the model:

- call the catalog first,
- create a spec second,
- do not claim data execution happened,
- keep source data read-only,
- ask clarification questions when needed.

## Where To Start Development

Start with the data contract before building advanced visuals.

1. Prepare a non-sensitive CRT Cost sample file.
   - CSV or XLSX is enough.
   - Include realistic column names and several dozen rows.

2. Create a data dictionary.
   - Column name.
   - Business meaning.
   - Data type.
   - Example values.
   - Allowed aggregations.
   - Common filters.
   - Any formulas that depend on the column.

3. Create a formula catalog.
   - CRT Cost formulas.
   - UPB-weighted formulas.
   - Partial-year normalization formula.
   - Edge cases.
   - Source document reference.

4. Upload the data dictionary and formula catalog to the knowledge base.

5. Add a deterministic dataset profiling tool.

6. Add a deterministic aggregation tool.

7. Only after that, connect the dashboard drawer to real aggregation output.

## Current Non-Goals

This branch does not yet:

- ingest the real CRT Cost database,
- execute SQL against source rows,
- calculate real dashboard values,
- validate production formulas,
- provide full dynamic chart editing,
- provide user-specific private dashboards,
- enforce row-level security.

Those should be added after the clean-source and catalog contracts are stable.
