# CLAUDE.md

## Project context

**Read `AGENTS.md` at the repo root before starting any work.** It is standalone: what this
project is, the architecture and the load-bearing decisions behind it, current verified state,
the defect-class checklist, and next steps. Do not reconstruct that context by reading the
codebase.

**Update `AGENTS.md` as the last step of every session.** Rewrite the affected sections so the
file describes the *current* state — do not append. It is a living snapshot, not a changelog.
If a session produced no state change, say so rather than padding it.

## Conventions established here — hold to them

**Zero-exception `scope_query`.** Every DB read and write on a tenant-scoped model goes through
`scope_query()`. Every route takes `principal: Principal = Depends(current_principal)`. Tenant
identity comes from the signed token, never from request input. `make test-tenancy` enforces
both at runtime and by AST sweep.

**Negative-control every new tenancy path before calling it verified.** Break the
`scope_query` call, run the suite, confirm it goes red and reports the actual leak, restore,
confirm green. A green suite that has never been shown to fail proves nothing. This has caught
real regressions more than once.

**Fix the defect class, not the instance.** When a bug is found, ask what shape of bug it is,
find every other instance, and add an automated guard so it cannot recur. Three classes are
tracked in `AGENTS.md` §4, each with its own checker. Two of those checkers were written after
the same bug appeared twice.

**Report as VERIFIED / FIXED / REMAINING BLOCKERS.** Every claim carries the command that
produced it. If something could not be verified, say why plainly instead of asserting success —
this environment has no network, so several things genuinely cannot be run here, and pretending
otherwise is worse than the gap.

**Keep the demo current.** `demo/tenant_isolation_demo.py` is the honest answer to "what works
right now?". Re-run it and extend it whenever new capability lands.

**Don't rewrite working code, refactor for style, or add speculative features.** Minimal
correct changes, verified.
