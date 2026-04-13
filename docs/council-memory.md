# Council Memory

*Shared workflow memory for fresh council sessions. This file captures how the
three-amigos process works in practice, beyond the raw prompt templates.*

This is not a replacement for `council/COUNCIL.md` or `council/COUNCIL_PROMPT.md`.
It exists to preserve the working habits that long-running chats tend to learn
and fresh sessions tend to miss.

---

## 1. What The Council Is Optimizing For

- independent evidence gathering first
- cross-checking before implementation
- narrow, implementable approved plans
- explicit deferrals instead of silent scope creep
- truthful separation between what is proven, what is inferred, and what is
  still a design choice

---

## 2. Practical Round Discipline

- Early rounds are often muddy. That is normal.
- Proposal phase should clarify the real disagreement before anyone starts
  acting like details are settled.
- Reviews should be evidence-heavy and cite exact file/line references.
- Approved plans should be specific enough that any of the three models could
  implement them without guessing.
- Closeout should capture minor non-blocking cleanup in `council/cleanup.md`
  rather than reopening finished rounds.

---

## 3. When To Use Follow-Up Round Tools

### Proposal-2

Use when initial proposals diverge but the disagreement is still mostly about
scope or framing.

### Review-2 / Evidence-Based Review

Use when the discussion needs stronger direct evidence before synthesis can be
useful.

### Secondary Synthesis

Use when the disagreement is already narrow and the goal is to resolve only the
remaining blocking issues.

### Investigation

Use when the blocking issue is genuinely unclear in the code/runtime and needs
deeper proof instead of more opinion.

---

## 4. Repeated Failure Modes In Council Work

- treating shorthand status text as if it were a final approved spec
- allowing late-raised side issues to derail a round that already has a narrow
  core objective
- mixing roadmap decisions with implementation decisions
- broadening a round to solve future architecture questions prematurely
- confusing “helpful future direction” with “in-scope for this round”

---

## 5. Review Tone And Scope Habits

- prefer pattern-level conclusions over vague architectural taste
- keep operator/logging additions minimal and security-conscious
- say “evidence missing” instead of filling in gaps with confident guesses
- if a design decision belongs in a future round, say so explicitly
- if something does not materially block approval, say so explicitly

---

## 6. Fresh Session Anchors

For a new council session, the shortest reliable reset path is usually:

1. `council/COUNCIL.md`
2. `council/COUNCIL_PROMPT.md`
3. `PROJECT_STATUS.md`
4. `CLAUDE.md`
5. this file
6. `docs/project-memory.md`
7. the active round folder

If a round references a roadmap or approved plan, read that before trusting
summary wording elsewhere.

---

## 7. Memory Update Rule

At closeout, ask:

- Did this round change something a fresh session would likely misread?
- Did it change the workflow, deployment reality, or project invariants?

If yes:

- update `docs/project-memory.md` and/or `docs/council-memory.md`

If no:

- leave them unchanged

These files should stay lean. If they become a second archive, they lose their
value.
