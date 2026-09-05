# LineageLens Development Workflow Harness

This file enforces the proper workflow for all issues. Follow this checklist before any work.

## PRE-WORK CHECKLIST

Before starting a new issue:
- [ ] Current work is committed and on feature branch
- [ ] Feature branch has been merged to main via PR
- [ ] Local main is up-to-date: `git pull origin main`
- [ ] Creating fresh feature branch from main: `git checkout -b feature/issue-NN-slug`

## ISSUE WORKFLOW (STRICT ORDER)

### Phase 1: SPECIFICATION (Before any code)
- [ ] Create `SPEC_ISSUE_NN.md` in project root (NOT in branch yet)
- [ ] Document: Why, Design, Implementation Plan, Testing Strategy, Success Criteria
- [ ] Get user approval on spec before proceeding
- [ ] **Only after approval**: Add spec to feature branch and commit

### Phase 2: IMPLEMENTATION
- [ ] Branch: On `feature/issue-NN-slug`
- [ ] Implement code according to approved spec
- [ ] Run tests: `python -m unittest discover tests -v`
- [ ] Verify: No regressions in existing tests
- [ ] Commit with clear message: `git commit -m "Issue #NN: <Title>"`

### Phase 3: CODE REVIEW
- [ ] Request code review: `./code-review high "Issue #NN: <description>"`
- [ ] Wait for review to complete
- [ ] Fix any bugs identified in review
- [ ] Re-run all tests
- [ ] Verify fixes don't introduce new issues
- [ ] Commit fixes: `git commit -m "Issue #NN: Fix <bug> from code review"`

### Phase 4: MERGE & CLEANUP
- [ ] Create PR: `gh pr create --title "Issue #NN: <Title>" --body "Fixes #NN"`
- [ ] All tests passing locally
- [ ] Code review approved
- [ ] Merge PR to main: `git merge --ff-only`
- [ ] Delete feature branch: `git branch -d feature/issue-NN-slug`
- [ ] Update MEMORY.md with completion status

## GIT WORKFLOW

### Starting New Issue
```bash
# 1. Ensure main is up-to-date
git checkout main
git pull origin main

# 2. Create feature branch
git checkout -b feature/issue-NN-issue-slug

# 3. Verify you're on the right branch
git branch -v  # Should show * feature/issue-NN-issue-slug
```

### During Development
```bash
# Commit regularly with atomic commits
git add <files>
git commit -m "Issue #NN: <Specific change>"

# NEVER commit directly to main
# ALWAYS work on feature branch
```

### After Code Review & Fixes
```bash
# Final commit after review fixes
git commit -m "Issue #NN: Fix <issue> from code review"

# Verify all tests pass
python -m unittest discover tests -v

# View commits
git log --oneline -5  # Should show issue #NN commits
```

### Merging to Main
```bash
# 1. Push feature branch
git push origin feature/issue-NN-slug

# 2. Create PR on GitHub
gh pr create --title "Issue #NN: <Title>" --body "Fixes #NN"

# 3. After PR is merged to main on GitHub:
git checkout main
git pull origin main

# 4. Verify issue #NN commits are now on main
git log --oneline -5  # Should show issue #NN commits

# 5. Delete feature branch locally and remote
git branch -d feature/issue-NN-slug
git push origin --delete feature/issue-NN-slug
```

## QUALITY GATES

Do NOT proceed to next phase if:
- ❌ Spec not approved by user
- ❌ Any test fails locally
- ❌ Code review finds critical bugs (must fix)
- ❌ Working on main branch directly (ALWAYS use feature branch)

## SPEC CHECKLIST (per issue)

Each spec must include:
- [ ] Executive Summary (why this matters)
- [ ] Design (how to solve it)
- [ ] Implementation Details (files, functions, logic)
- [ ] Testing Strategy (test cases, coverage)
- [ ] Success Criteria (what done looks like)
- [ ] Dependencies (what issues must complete first)
- [ ] Review Checklist (final verification before merge)

## TEMPLATE: Issue NN Workflow

```
Issue: #NN - <Title>
Status: SPEC (awaiting approval) | IN-PROGRESS | CODE-REVIEW | MERGED

Current Branch: feature/issue-NN-slug
Last Commit: <commit-hash>
Tests: 0/N passing

TODO:
- [ ] Spec approved
- [ ] Implementation complete
- [ ] All tests passing
- [ ] Code review done
- [ ] Merged to main
```

## How to Use This Harness

**Before starting work on an issue:**
1. Read this entire file
2. Follow the phase checklist strictly
3. Do NOT skip phases
4. Do NOT work on main directly
5. Ask user for spec approval before coding

**When stuck:**
- Refer to the GIT WORKFLOW section
- Refer to the QUALITY GATES section
- Ask for clarification on workflow

**Success Indicator:**
When an issue is complete, the workflow looks like:
```
main branch: 08b4f92 Merge pull request #32 from pranayVyas/feature/issue-NN-title
```
