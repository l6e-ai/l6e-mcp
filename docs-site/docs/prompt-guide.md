---
id: prompt-guide
title: Prompt Guide
sidebar_label: Prompt Guide
sidebar_position: 2
---

# Prompt Guide

Getting the best results from l6e comes down to how you prompt. This page covers the practical patterns that make budget enforcement reliable.

:::tip TL;DR
End any message where you want enforcement with **`budget $X`**. That single habit covers 80% of cases.
:::

## Rule setup

### Always-apply (recommended)

Set the rule to always apply (Cursor: `alwaysApply: true` in `.mdc` frontmatter; Claude Code: place in `CLAUDE.md`). The agent picks up the rule automatically every conversation.

For quick tasks where you don't want enforcement overhead, start your message with:

```
Skip l6e. <your task here>
```

### On-demand

Set the rule to not always apply. Include it as context when you want enforcement:

- **Cursor:** @ mention the rule file (e.g. `@l6e-budget-enforcement`)
- **Claude Code:** Reference the `CLAUDE.md` section

Better for developers who rarely want enforcement and prefer to opt in.

## Core prompting patterns

### `budget $X`

End your message with a budget amount. This is the single most effective pattern:

```
Implement the changes from the plan above. Budget $3.
```

Works in follow-up messages, not just the first message. If you're using always-apply, this is all you need. If you're using on-demand, make sure the rule was loaded earlier in the conversation.

### Checkpoint instructions

For multi-step work, add explicit instructions for when the agent should check the budget:

```
Implement this plan. Between each todo item, call l6e_authorize_call
to check budget. Budget $5.
```

```
Review the implementation for correctness. Call l6e_run_status after
reading each file. Budget $2.
```

### `skip l6e`

For trivial tasks where enforcement isn't worth the overhead:

```
Skip l6e. Rename the variable `foo` to `bar` in utils.py.
```

## Feature lifecycle example

A typical feature flows through plan → review → implement → verify. Here's how to prompt through each phase.

### 1. Plan

```
I need to add rate limiting to the /api/upload endpoint. Start an l6e
session and write a plan. Budget $1.
```

### 2. Review and revise the plan

```
The plan looks good but I want to use a sliding window instead of
fixed buckets. Update the plan. Budget $1.
```

### 3. Implement

```
Plan looks good. Implement it. Call l6e_authorize_call before each
major step. Budget $5.
```

### 4. Review the implementation

```
Review what you just implemented for correctness and edge cases.
Budget $2.
```

### 5. Fix issues

```
Fix the race condition you identified in the review. Budget $2.
```

### Single-message variant

If you prefer to run the full lifecycle in one shot:

```
I need to add rate limiting to the /api/upload endpoint using a
sliding window algorithm.

1. Write a plan
2. Review it yourself for completeness
3. Implement it
4. Review the implementation

Call l6e_authorize_call at each phase transition. Budget $8.
```

## Budget sizing

| Task type | Suggested budget | Notes |
|---|---|---|
| Exploration / Q&A | $0.50 – $1 | Reading files, answering questions |
| Planning | $1 – $2 | Drafting and revising a plan |
| Small implementation | $2 – $3 | A few files, straightforward changes |
| Large implementation | $3 – $8 | Multi-file, sub-agents, tests |
| Full lifecycle | $5 – $10 | Plan through review in one conversation |
| Quick one-off edit | Skip l6e | Not worth the overhead |

Start small and increase if the agent halts mid-task — it preserves full context and tells you what remains, so you can approve more budget without starting over.

## More patterns

### Re-engaging enforcement mid-conversation

```
You stopped checking the budget. Call l6e_run_status now to check
spend, then continue. Budget $3.
```

### Starting a fresh session mid-conversation

```
Start a new l6e session with budget $3 and implement the remaining
items from the todo list.
```

### Limiting sub-agent usage

Sub-agents are the most expensive single operations. To keep costs down:

```
Implement the auth middleware changes. Do not use sub-agents — do all
work directly. Budget $3.
```

## Quick reference

1. **`budget $X`** at the end of your message — the single most important habit
2. **Checkpoint instructions** for multi-step work — `call l6e_authorize_call between each step`
3. **`skip l6e`** for trivial tasks
4. **Start small** — increase budget if the agent halts; context is preserved
