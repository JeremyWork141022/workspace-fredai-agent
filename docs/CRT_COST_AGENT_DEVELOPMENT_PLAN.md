# CRT Cost Agent Development Plan

This branch prepares a single-process `CRT Cost Agent` prototype. The goal is to
use CRT Cost as the first complete showcase before generalizing the same pattern
back to broader CRT Analytics workflows.

## Branch

```text
codex/crt-cost-agent
```

## Current Branch Defaults

- UI name: `CRT Cost Agent`
- Default knowledge base: `CRT Cost`
- Active curated memory: `.runtime/memories/MEMORY.md`
- FredAI remains the only model gateway.
- Existing thread, knowledge-base, wiki, feedback, hooks, upload, and drawer
  infrastructure remain in place.
- Formula rendering now prefers local MathJax if the approved browser bundle is
  copied to `web/vendor/mathjax/tex-mml-chtml.js`.

## What The Agent Is Being Built To Understand

The initial data product is a deal-level CRT Cost database:

- One row represents one deal.
- Important fields likely include:
  - deal identifier,
  - CRT Cost,
  - UPB,
  - payoff date,
  - settle year,
  - deal type,
  - other model/process features used for grouping or derived calculations.
- Expected outputs include:
  - aggregation tables,
  - charts,
  - filters,
  - derived columns,
  - formula-backed normalized metrics,
  - explanation of each metric and dashboard view.

## Where To Start Development

Start with data clarity before UI charts.

1. Create a small non-sensitive sample CSV or Excel workbook.
   - 20 to 100 rows is enough.
   - Include representative columns, not production secrets.
   - Include edge cases: missing dates, partial-year records, multiple deal
     types, zero UPB, unusual settle years.

2. Draft a data dictionary.
   - Column name.
   - Business meaning.
   - Data type.
   - Example value.
   - Required or optional.
   - Allowed values if categorical.
   - Formula role if the field feeds a calculation.

3. Draft a metric and formula catalog.
   - Raw CRT Cost.
   - UPB aggregation.
   - CRT Cost by payoff date.
   - CRT Cost by settle year.
   - CRT Cost by deal type.
   - UPB-weighted or normalized CRT Cost.
   - Partial-year normalization rule.
   - Any denominator, date basis, or exclusion rule.

4. Upload the data dictionary and formula catalog to the knowledge base.
   - Use the Knowledge Base drawer or chat upload.
   - Confirm they are indexed under the `CRT Cost` knowledge base.
   - Ask source-backed questions to verify retrieval.

5. Only then add dashboard tools.
   - First implement backend table aggregation.
   - Then return JSON chart specs.
   - Then render charts in the drawer or chat.

## Recommended Backend Tool Roadmap

Keep tools small and deterministic. The LLM should decide what to run, but the
math should be code-owned.

### Phase 1: Data Inspection

Add tools:

- `crt_cost_load_dataset`
  - Load an approved local CSV/XLSX path or uploaded dataset reference.
  - Return dataset ID, row count, column list, inferred types, and warnings.

- `crt_cost_profile_dataset`
  - Return null counts, distinct counts, numeric ranges, date ranges, and likely
    categorical fields.

- `crt_cost_get_dictionary`
  - Read indexed data dictionary/wiki definitions for selected fields.

### Phase 2: Aggregation

Add tools:

- `crt_cost_aggregate`
  - Inputs: dataset ID, metric, group-by fields, filters, aggregation function.
  - Outputs: table rows plus metadata explaining grain and filters.

- `crt_cost_upb_summary`
  - Purpose-built UPB aggregation with filters.
  - Useful because UPB is core enough to deserve a clear, auditable tool.

### Phase 3: Formulas

Add tools:

- `crt_cost_formula_catalog`
  - List approved formulas from wiki/knowledge base.

- `crt_cost_compute_column`
  - Inputs: dataset ID, formula ID, output column name.
  - Runs only approved formulas implemented in Python.
  - Rejects arbitrary user-provided code.

- `crt_cost_normalize_partial_year`
  - Dedicated tool for partial-year CRT Cost normalization once the exact rule is
    documented and approved.

### Phase 4: Dashboard

Add tools:

- `crt_cost_dashboard_spec`
  - Builds a dashboard JSON model: filters, tables, charts, metric cards.

- `crt_cost_chart_data`
  - Returns chart-ready series for a selected dashboard component.

The UI drawer should later add a `dashboard` view beside the existing
`knowledge` view. The runtime hook system can open that view when dashboard
tools run, exactly like the Knowledge drawer opens when knowledge tools run.

## Frontend Roadmap

Do not start with a heavy dashboard framework. The work computer may block
packages, and the first need is correctness.

1. Start with HTML tables rendered from backend JSON.
2. Add simple SVG or Canvas charts only after aggregation output is stable.
3. Add a right-drawer `dashboard` view later:
   - filter controls,
   - metric cards,
   - aggregation tables,
   - chart panels,
   - export/download buttons.
4. Keep chart specs data-driven so the backend can produce dashboard views
   without hardcoding one chart at a time.

## Knowledge Base Plan

The CRT Cost knowledge base should contain:

- data dictionary,
- source methodology,
- formula catalog,
- dashboard requirements,
- change memos,
- field definitions,
- glossary entries,
- known corrections and review issues.

Raw source documents stay raw. Corrections and explanations should go into the
wiki layer after review.

## First Test Questions

After uploading the first data dictionary and methodology document, test:

```text
What columns define the CRT Cost database?
```

```text
What is CRT Cost, and which source says that?
```

```text
Aggregate CRT Cost by settle year. What fields do you need before running this?
```

```text
What formula should be used to normalize partial-year CRT Cost?
```

Expected behavior:

- If the answer is in indexed documents, the agent should cite source snippets.
- If the answer is not defined, the agent should say so and log/suggest a
  wiki_issue instead of inventing a definition.
- If a formula is missing, the agent should not invent it.

## Development Order

Recommended next work session:

1. Confirm branch: `git branch --show-current`.
2. Confirm UI starts in mock mode.
3. Confirm MathJax local asset policy with the work computer.
4. Prepare the sample CRT Cost data dictionary.
5. Upload/index the data dictionary.
6. Add the first deterministic backend tool: `crt_cost_profile_dataset`.
7. Add one test fixture CSV.
8. Add unit tests for profile output.
9. Add the first aggregation tool.
10. Only then design the first dashboard drawer view.
