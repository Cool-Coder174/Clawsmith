# ClawSmith Task Prompt

## Role
You are a senior software engineer working on the ClawSmith orchestration system.

## Objective
Fix the login authentication bug in the user module.

## Repository Architecture
Languages: .py (42 files)
Frameworks: (none detected)
Package managers: pip
Build systems: setuptools
Test frameworks: pytest

## Relevant Files
### orchestrator/pipeline.py
[file contents here]

## Build & Test Commands
- python: pip install -e .[dev]
- python: pytest
- python: ruff check .

## Acceptance Criteria
- The login function no longer raises KeyError when session is missing
- All existing tests pass
- No new ruff lint errors introduced

## Expected Changed Files
- orchestrator/pipeline.py

## Constraints
- Token budget: 8000
- Do not modify unrelated files
