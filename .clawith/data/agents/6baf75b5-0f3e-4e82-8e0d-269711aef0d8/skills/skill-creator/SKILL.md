---
name: skill-creator
description: Create new skills, modify and improve existing skills, and measure skill performance. Use when users want to create a skill from scratch, update or optimize an existing skill, run evals to test a skill, benchmark skill performance with variance analysis, or optimize a skill's description for better triggering accuracy.
---

# Skill Creator

A skill for creating new skills and iteratively improving them.

At a high level, the process of creating a skill goes like this:

- Decide what you want the skill to do and roughly how it should do it
- Write a draft of the skill
- Create a few test prompts and run claude-with-access-to-the-skill on them
- Help the user evaluate the results both qualitatively and quantitatively
- Rewrite the skill based on feedback from the user's evaluation
- Repeat until you're satisfied
- Expand the test set and try again at larger scale

Your job when using this skill is to figure out where the user is in this process and then jump in and help them progress through these stages.

## Communicating with the user

Pay attention to context cues to understand how to phrase your communication. Briefly explain terms if you're in doubt, and feel free to clarify terms with a short definition if you're unsure if the user will get it.

---

## Creating a skill

### Capture Intent
Start by understanding the user's intent.

1. What should this skill enable the agent to do?
2. When should this skill trigger? (what user phrases/contexts)
3. What's the expected output format?
4. Should we set up test cases to verify the skill works?

### Interview and Research
Proactively ask questions about edge cases, input/output formats, example files, success criteria, and dependencies. Wait to write test prompts until you've got this part ironed out.

### Write the SKILL.md
Based on the user interview, fill in these components:

- **name**: Skill identifier
- **description**: When to trigger, what it does. This is the primary triggering mechanism - include both what the skill does AND specific contexts for when to use it.
- **the rest of the skill**

### Skill Writing Guide

#### Anatomy of a Skill

```
skill-name/
\u251c\u2500\u2500 SKILL.md (required)
\u2502   \u251c\u2500\u2500 YAML frontmatter (name, description required)
\u2502   \u2514\u2500\u2500 Markdown instructions
\u2514\u2500\u2500 Bundled Resources (optional)
    \u251c\u2500\u2500 scripts/    - Executable code for deterministic/repetitive tasks
    \u251c\u2500\u2500 references/ - Docs loaded into context as needed
    \u2514\u2500\u2500 assets/     - Files used in output (templates, icons, fonts)
```

#### Progressive Disclosure

Skills use a three-level loading system:
1. **Metadata** (name + description) - Always in context (~100 words)
2. **SKILL.md body** - In context whenever skill triggers (<500 lines ideal)
3. **Bundled resources** - As needed (unlimited, scripts can execute without loading)

**Key patterns:**
- Keep SKILL.md under 500 lines; if approaching this limit, add hierarchy with clear pointers
- Reference files clearly from SKILL.md with guidance on when to read them
- For large reference files (>300 lines), include a table of contents

#### Writing Patterns

Prefer using the imperative form in instructions.

### Writing Style
Explain to the model why things are important. Use theory of mind and try to make the skill general. Start by writing a draft and then look at it with fresh eyes and improve it.

### Test Cases
After writing the skill draft, come up with 2-3 realistic test prompts. Share them with the user. Save test cases to `evals/evals.json`.

---

## Running and evaluating test cases

This section is one continuous sequence.

### Step 1: Run test cases
For each test case, run the agent with the skill applied, and optionally a baseline run without the skill for comparison.

### Step 2: Draft assertions
While runs are in progress, draft quantitative assertions for each test case. Good assertions are objectively verifiable and have descriptive names.

### Step 3: Capture timing data
When each run completes, save timing data (tokens, duration) to `timing.json`.

### Step 4: Grade, aggregate, and launch the viewer
Once all runs are done:
1. Grade each run against assertions — see `agents/grader.md`
2. Aggregate results: `python -m scripts.aggregate_benchmark <workspace>/iteration-N --skill-name <name>`
3. Launch the viewer: `python eval-viewer/generate_review.py <workspace>/iteration-N --skill-name "my-skill" --benchmark <workspace>/iteration-N/benchmark.json`
4. Present results to the user for review

### Step 5: Read the feedback
Read user feedback from `feedback.json`. Empty feedback means the user thought it was fine.

---

## Improving the skill

### How to think about improvements
1. **Generalize from the feedback.** Don't overfit to specific examples.
2. **Keep the prompt lean.** Remove things that aren't pulling their weight.
3. **Explain the why.** Today's LLMs are smart. Explain reasoning rather than rigid MUSTs.
4. **Look for repeated work across test cases.** Bundle common scripts in `scripts/`.

### The iteration loop
1. Apply improvements to the skill
2. Rerun all test cases into a new iteration directory
3. Present results for review
4. Wait for user to review
5. Read feedback, improve again, repeat

---

## Advanced: Blind comparison
For rigorous comparison between two versions. Read `agents/comparator.md` and `agents/analyzer.md`.

## Description Optimization
Optimize the description for better triggering accuracy. Use `scripts/run_loop.py`.

---

## Reference files

- `agents/grader.md` — How to evaluate assertions against outputs
- `agents/comparator.md` — How to do blind A/B comparison between two outputs
- `agents/analyzer.md` — How to analyze why one version beat another
- `references/schemas.md` — JSON structures for evals.json, grading.json, etc.
- `assets/eval_review.html` — HTML template for eval review
- `eval-viewer/generate_review.py` — Script to generate the review viewer
- `scripts/aggregate_benchmark.py` — Aggregate benchmark results
- `scripts/generate_report.py` — Generate optimization report
- `scripts/improve_description.py` — Improve skill description
- `scripts/package_skill.py` — Package skill for distribution
- `scripts/quick_validate.py` — Quick validation
- `scripts/run_eval.py` — Run triggering evaluation
- `scripts/run_loop.py` — Run optimization loop
- `scripts/utils.py` — Shared utilities
