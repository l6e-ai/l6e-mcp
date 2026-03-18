---
id: prompt-guide
title: Prompt Guide
sidebar_label: Prompt Guide
sidebar_position: 2
---

# Prompt Guide

:::caution The main thing to know
The l6e enforcement rule lives in your editor's context window alongside everything else — your code, tool results, conversation history. **As context grows, the agent increasingly deprioritizes or silently drops the rule.** This is not a bug in l6e; it is how LLM context attention works today.

The single most effective fix: **end any message where you want budget enforcement with `budget $X`**. This puts the budget instruction at the bottom of the context — the position LLMs attend to most strongly.
:::

## Rule setup modes

There are two ways to configure the l6e rule, and which you choose affects how you prompt.

### Always-apply (recommended for most users)

Set the rule to always apply (Cursor: `alwaysApply: true` in the `.mdc` frontmatter; Claude Code: place in `CLAUDE.md`). The agent sees the rule automatically at the start of every conversation.

**Trade-off:** The rule is injected even for quick one-off tasks where you don't want budget enforcement. For those, start your message with:

```
Skip l6e. <your quick task here>
```

### On-demand

Set the rule to not always apply. When you want budget enforcement, explicitly include it as context at the start of the conversation:

- **Cursor:** `@l6e-budget-enforcement` (@ mention the rule file)
- **Claude Code:** Reference the CLAUDE.md section or paste the rule

**Trade-off:** You must remember to include it. If you forget, the agent won't enforce budgets at all. This mode is better for developers who rarely want enforcement and prefer to opt in.

## Keeping enforcement alive in long conversations

The rule is strongest at conversation start. As you send follow-up messages — especially after large tool outputs, plan reviews, or implementation steps — the agent's attention to the rule fades. Sometimes it consciously decides to skip l6e for "quick" follow-ups; more often, the rule simply isn't prominent enough in context anymore.

### The `budget $X` pattern

End your message with a budget declaration. This works regardless of rule setup mode (as long as the rule was loaded at some point in the conversation):

```
Implement the changes from the plan above. Budget $3.
```

The agent reads `budget $3` at the tail of its context, where attention is highest, and re-engages the l6e lifecycle.

### Adding granular enforcement instructions

For multi-step work where you want tighter control, add explicit checkpoint instructions alongside the budget:

```
Implement this plan. Between each todo item, call l6e_authorize_call
to check budget. Budget $5.
```

```
Review the implementation for correctness. Call l6e_run_status after
reading each file. Budget $2.
```

This is especially useful when the agent is working through a todo list — without the reminder, it may batch multiple items without checking.

## Prompt examples for a full feature lifecycle

A typical feature scope in a single conversation window flows through several phases: plan, review, revise, implement, verify. Here is how to use l6e throughout.

### 1. Start the conversation — set the scope

```
I need to add rate limiting to the /api/upload endpoint. Start an l6e
session and write a plan. Budget $1.
```

The agent calls `l6e_run_start`, drafts a plan, and gates its work. A $1 budget is usually enough for planning and exploration.

### 2. Review the plan — ask for changes

```
The plan looks good but I want to use a sliding window instead of
fixed buckets. Update the plan. Budget $1.
```

You can keep the same l6e session or let the agent start a new one — either works. The key is that `budget $1` re-engages enforcement for this follow-up.

### 3. Approve and implement

```
Plan looks good. Implement it. Call l6e_authorize_call before each
major step. Budget $5.
```

Implementation is the most expensive phase. A larger budget and explicit checkpoint instructions keep the agent disciplined through multi-file changes.

### 4. Review the implementation

```
Review what you just implemented for correctness and edge cases.
Budget $2.
```

### 5. Fix issues from review

```
Fix the race condition you identified in the review. Budget $2.
```

### Full single-message variant

If you prefer to run the entire lifecycle in one shot:

```
I need to add rate limiting to the /api/upload endpoint using a
sliding window algorithm.

1. Write a plan
2. Review it yourself for completeness
3. Implement it
4. Review the implementation

Call l6e_authorize_call at each phase transition. Budget $8.
```

## Budget sizing guidelines

| Task type | Suggested budget | Notes |
|---|---|---|
| Exploration / Q&A | $0.50 – $1 | Reading files, answering questions |
| Planning | $1 – $2 | Drafting and revising a plan |
| Implementation (small) | $2 – $3 | A few files, straightforward changes |
| Implementation (large) | $3 – $8 | Multi-file, sub-agents, tests |
| Full lifecycle (plan → implement → review) | $5 – $10 | Everything in one conversation |
| Quick one-off edit | Skip l6e | Not worth the overhead |

These are rough starting points. Actual costs depend on your model, context size, and how many sub-agents the editor spawns. Start small and increase if the agent halts mid-task — it will tell you what it still needs to do, and you can approve more budget without losing context.

## Common patterns

### "Skip l6e" for trivial tasks

```
Skip l6e. Rename the variable `foo` to `bar` in utils.py.
```

The agent sees "skip l6e" and does not call any MCP tools. No session overhead for a 10-second edit.

### Re-engaging after the agent drops enforcement

If you notice the agent stopped calling l6e tools mid-conversation:

```
You stopped checking the budget. Call l6e_run_status now to check
spend, then continue. Budget $3.
```

### Starting fresh mid-conversation

If the previous session ended or you want a clean budget:

```
Start a new l6e session with budget $3 and implement the remaining
items from the todo list.
```

### Scoping sub-agent usage

Sub-agents (Cursor's Task tool, Claude Code's parallel agents) are the most expensive operations because they make their own chains of tool calls with unpredictable cost. If you're budget-conscious:

```
Implement the auth middleware changes. Do not use sub-agents — do all
work directly. Budget $3.
```

## Summary

1. **Set up the rule** — always-apply is simplest, on-demand gives more control
2. **End messages with `budget $X`** — this is the single most important habit
3. **Add checkpoint instructions for multi-step work** — `call l6e_authorize_call between each step`
4. **Use `skip l6e` for trivial tasks** — no overhead for quick edits
5. **Size budgets to the task** — start small, increase if halted
