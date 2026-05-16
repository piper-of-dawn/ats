# Commit Change Set

- Base commit: `b2f0728`
- Target commit: `5e15d3e`
- Changed paths: `18`

Graphify snapshot layout:
- `before/` contains changed files as they existed at the base commit.
- `after/` contains changed files as they existed at the target commit.
- Deleted files only appear in `before/`.
- Added files only appear in `after/`.

## File status summary

| Status | Path |
| --- | --- |
| `M` | `Dockerfile` |
| `M` | `dashboard/data.py` |
| `M` | `dashboard/static/css/dashboard.css` |
| `M` | `dashboard/static/js/dashboard.js` |
| `M` | `dashboard/templates/table.html` |
| `A` | `function-augmentation-decorators.md` |
| `R071` | `jenkins/Jenkinsfile.UpdateRating -> jenkins/Jenkinsfile.ratings` |
| `M` | `jenkins/Jenkinsfile.run` |
| `M` | `jenkins/casc/jobs/ats-jobs.groovy` |
| `M` | `pyproject.toml` |
| `M` | `src/ats/dataIO/supabase_integration.py` |
| `A` | `src/ats/dataIO/utils.py` |
| `A` | `src/ats/fundamentals/analyst_price_targets.py` |
| `M` | `src/ats/fundamentals/analyst_ratings.py` |
| `A` | `src/ats/fundamentals/combined_score.py` |
| `M` | `src/ats/helpers.py` |
| `M` | `src/ats/ticker.py` |
| `A` | `tests/test_helpers.py` |

## Diff stat

```text
Dockerfile                                         |  14 +-
 dashboard/data.py                                  |  56 ++--
 dashboard/static/css/dashboard.css                 |  58 +++-
 dashboard/static/js/dashboard.js                   | 163 +++++++++-
 dashboard/templates/table.html                     |  35 +-
 function-augmentation-decorators.md                | 355 +++++++++++++++++++++
 ...enkinsfile.UpdateRating => Jenkinsfile.ratings} |  20 +-
 jenkins/Jenkinsfile.run                            |  25 +-
 jenkins/casc/jobs/ats-jobs.groovy                  |  26 +-
 pyproject.toml                                     |   2 +
 src/ats/dataIO/supabase_integration.py             |  68 +++-
 src/ats/dataIO/utils.py                            |  85 +++++
 src/ats/fundamentals/analyst_price_targets.py      |  61 ++++
 src/ats/fundamentals/analyst_ratings.py            |  84 +++--
 src/ats/fundamentals/combined_score.py             | 150 +++++++++
 src/ats/helpers.py                                 | 134 +++++++-
 src/ats/ticker.py                                  |  26 +-
 tests/test_helpers.py                              |  94 ++++++
 18 files changed, 1307 insertions(+), 149 deletions(-)
```
