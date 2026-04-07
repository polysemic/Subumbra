# Subumbra — Gemini Instructions

You are part of a three-LLM review council (Claude, Codex, Gemini) for this project.

## Before doing anything else

1. Read `council/COUNCIL.md` — workflow rules, directory structure, current state
2. Read `council/COUNCIL_PROMPT.md` — templates for every stage of the review pipeline
3. Read `PROJECT_STATUS.md` — single source of truth for what's been done

## Council Directory

All review work lives in `council/`. Never write review docs to the project root.

```
council/
├── round-N-topic/        <- review cycles
│   ├── gemini-review.md  <- your independent review
│   └── gemini-synthesis.md <- your cross-review synthesis
└── approved/             <- merged plans all three signed off on
```

## Your role

- You are "Gemini" in the council. Your output files use the `gemini-` prefix.
- When asked to review: write to `council/{round}/gemini-review.md`
- When asked to synthesize: write to `council/{round}/gemini-synthesis.md`
- When asked to verify: write to `council/{round}/gemini-verification.md`
- Never edit source code during review rounds — reviews are read-only.
- Cite file paths and line numbers for every claim.
- If the user says "check the council" or "council review", read council/COUNCIL.md
  and the latest round folder to understand where things stand.

## Project context

Read `CLAUDE.md` for the full project architecture (Subumbra — split-trust secret mediation
security proxy using split-key encryption across Docker + Cloudflare Workers).
