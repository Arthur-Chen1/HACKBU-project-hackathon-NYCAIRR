# Priority cleanup history and compatibility

Updated November 8, 2026. Current behavior is documented in [Dimming planning estimates](DIMMING_ESTIMATES.md).

The first cleanup combined the separate absolute-priority and relative-risk map modes into one
20% high / 30% medium / 50% low priority scale. The user then reported too many red buildings and
requested roughly 5% red plus a dimming number driven by the collision AI.

After the limits of the available lighting data were explained, the user explicitly selected an
AI-driven planning estimate labeled as an unvalidated rule of thumb. The website now displays
suggested dimming percentages instead of collision-priority labels. The old 20/30/50 allocation
and tie-expansion behavior no longer apply.

The shared ranking helper in `src/recommendations/priority.py` reserves at most rounded-up 5% for
red and 25% for red/amber combined, retaining ties in the lower band when they cross a boundary.
The `risk_priority` and `priority_*` fields remain only for export/API/CLI compatibility and use
this same ranking. The website reads `dimming_band` and `dimming_rank`.

The original operator-budget off/dim/reduce controls remain command-line-only. The new planning
suggestion is a separate rule, not an assertion that the AI has learned dimming effectiveness.
See the [policy, source review, data fields, and verification](DIMMING_ESTIMATES.md).
