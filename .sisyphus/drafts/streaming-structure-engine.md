# DRAFT: Streaming StructureEngine — POST-MOMUS

**Status**: Plan updated at `.sisyphus/plans/streaming-structure-engine.md`

## Momus Fixes Applied (all 7)
- **C1** ✅ Fixed S1/S2 mapping — `last_positions[-2]` = S1 = `_swings[-3]`, not S2
- **C2** ✅ Added "Known Limitations — Timing Gap" subsection with break-in-the-gap, end-of-dataset limbo, close_break asymmetry
- **C3** ✅ Same-bar event dedup in T5: `extend(e for e in status_changes if e not in new_events)`
- **C4** ✅ Added `simulator is not None` guard in strategy; QA scenarios use real `TradeSimulator()`
- **C5** ✅ Removed duplicate `events` property
- **C6** ✅ Already present in Known Limitations ("End-of-dataset limbo")
- **C7** ✅ Already present in Known Limitations ("close_break asymmetry")

## Clearance Checklist
□ Core objective: ✅ CLEAR
□ Scope boundaries: ✅ ESTABLISHED
□ Critical ambiguities: ✅ RESOLVED
□ Technical approach: ✅ DECIDED and DOCUMENTED (with limitations)
□ Test strategy: ✅ CONFIRMED
□ Blocking questions: ✅ NONE — plan is implementation-ready
