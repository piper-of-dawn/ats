# Graphify PR Review Skill

## Purpose

Use this skill when reviewing a pull request with Graphify artifacts generated from a commit range.

Your job is to find concrete bugs, regressions, risky architectural changes, suspicious cross-module effects, and missing tests.

Do not spend most of the answer summarizing the PR. Findings come first.

## Inputs

You may be given some or all of these files:

1. `GRAPH_REPORT.md`
2. `CHANGESET.md`
3. `graph.json`
4. Raw changed files

Treat them in that priority order.

## How To Read The Artifacts

### 1. `GRAPH_REPORT.md`

This is the primary artifact.

Use it to identify:

- god nodes: high-degree functions/classes/modules where regressions are more dangerous
- surprising connections: cross-module or unexpected edges that deserve extra scrutiny
- communities: clusters of related logic that tell you what changed together

Read this file first and use it to decide where to look next.

### 2. `CHANGESET.md`

Use this to understand:

- exact file scope
- rename/add/delete status
- overall diff size
- before/after snapshot layout

Do not invent files or changes outside this file.

### 3. `graph.json`

Use this only if you need more structure than the markdown report gives you.

Use it to:

- inspect exact nodes and edges
- verify whether a surprising connection is isolated or part of a larger cluster
- trace whether a changed symbol touches a critical hub

Do not dump the JSON back to the user. Use it for reasoning.

### 4. Raw changed files

Use raw files only after the report points you to likely risk areas.

Prefer targeted inspection over broad summarization.

## Review Process

1. Read `GRAPH_REPORT.md`.
2. Read `CHANGESET.md`.
3. First search for showstoppers.
4. If no showstoppers exist, search for concrete critical vulnerabilities.
5. Form a short list of risky modules, hubs, and suspicious connections.
6. Inspect raw files only for those risky areas.
7. Produce findings ordered by severity.

## Severity Gate

### 1. Showstoppers

Check this first.

A showstopper is a rare, high-confidence issue that makes the change unsafe to merge or deploy. This bar is very high.

Examples:

- obvious data corruption risk
- guaranteed runtime failure on the main path
- schema mismatch that will definitely break production reads or writes
- destructive behavior triggered by normal usage

Do not label something a showstopper unless the failure mechanism is concrete and highly likely.

If there is no real showstopper, say so plainly.

### 2. Critical Vulnerabilities

Only check this after the showstopper pass.

Only report critical vulnerabilities when they are real, concrete, and materially dangerous.

Examples:

- credible secret exposure
- SQL injection or command injection with a realistic input path
- authentication or authorization bypass
- unsafe file or shell behavior with a believable exploitation path

Do not highlight vulnerabilities for the sake of highlighting vulnerabilities.
Do not escalate weak hygiene issues into vulnerabilities.
Do not speculate about exploitability unless the path is technically defensible.

## What Counts As A Good Finding

A good finding is:

- specific
- technically defensible
- tied to a real file, symbol, path, or behavior
- clear about the consequence

Each finding should explain:

- what changed
- why it is risky or wrong
- what behavior could break

If the finding is a vulnerability, it must also explain:

- the input path or trust boundary
- the exploit mechanism
- why the impact is material

## What To Prioritize

Prioritize these first:

- genuine showstoppers
- genuine critical vulnerabilities
- behavior changes around god nodes
- broken assumptions across before/after module boundaries
- missing updates to related callers or readers
- schema/data-flow mismatches
- test gaps around newly central logic
- renamed or moved logic that may have stale references
- inferred or surprising cross-community edges

## What To Avoid

Avoid:

- long PR summaries before findings
- vague style commentary
- repeating the graph report without analysis
- claiming a bug without a concrete mechanism
- speculative security findings without an exploit path
- inflated severity labels
- reviewing unchanged parts of the repo without reason

## Output Format

Start with findings.

For each finding, use this structure:

`[severity] file_or_symbol`

- what changed
- why it is risky or incorrect
- likely impact

After findings, optionally include:

- open questions
- residual risks
- summary sections listed below

If no findings are discovered, say so explicitly and mention residual risk or missing verification coverage.

## Required Summary Sections

After findings, always include these two sections:

### 1. Impact On Output

Describe how the change affects user-visible output, computed results, database contents, dashboard behavior, reports, or external behavior.

If there is no meaningful output impact, say that clearly.

### 2. Impact On Code Vulnerability

Describe whether the change materially increases, decreases, or does not meaningfully change vulnerability risk.

Rules:

- if there is no real vulnerability signal, say that clearly
- do not manufacture security concerns
- do not include low-value security commentary just to fill the section
- keep this section short unless there is a real issue

## Recommended Final Structure

Use this order:

1. `Showstopper Check`
2. `Critical Vulnerability Check`
3. `Findings`
4. `Impact On Output`
5. `Impact On Code Vulnerability`
6. `Open Questions` or `Residual Risks` if needed

## Operating Rule

The graph is a guide, not proof.

Treat `GRAPH_REPORT.md` as a prioritization tool. Confirm important claims against `CHANGESET.md` and, when needed, the raw changed files.
