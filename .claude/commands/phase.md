---
description: Implement one phase of PLAN.md following the project workflow rules
---

Implement phase $ARGUMENTS of this project.

1. Read CLAUDE.md (workflow rules), STATE.md (current status and carried-over open
   questions), and the phase's section in PLAN.md before touching anything.
2. Confirm the phase's predecessors are `done` in STATE.md; if the requested phase is
   already done or its predecessor is not, stop and tell the user.
3. Do the phase's tasks. Record any deviation from PLAN.md in STATE.md before proceeding.
4. Verify every acceptance criterion listed for the phase.
5. Run the end-of-phase ritual from CLAUDE.md: update STATE.md, add a CHANGELOG.md entry,
   conventional commit, push, then give the plain-language summary and the go/no-go
   statement for the next phase.

Never start the next phase in the same conversation.
