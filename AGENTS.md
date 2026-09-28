# Repository Guidelines

## Current Repository State

This directory is currently empty and is not a Git repository. No source tree, assets, manifests, build tools, or tests exist yet. Do not assume commands or frameworks until they are added; update this guide in the same change that introduces project tooling.

## Project Structure & Module Organization

When scaffolding the project, keep structure explicit and documented here. Recommended defaults:

- `src/` for production source code.
- `tests/` for tests; mirror the `src/` hierarchy where useful.
- `assets/` for static, non-generated assets.
- `scripts/` for repeatable development or data tasks.
- `docs/` for design and operational documentation.

Keep modules focused on one responsibility. Avoid catch-all `utils` modules and unexplained generated files.

## Build, Test, and Development Commands

No commands are currently defined. After selecting a stack, add pinned dependencies and document exact root-level commands here, such as:

- `make setup` or `npm install` installs dependencies.
- `make test` or `npm test` runs the complete test suite.
- `make lint` or `npm run lint` checks formatting and style.
- `make run` or `npm run dev` starts the local application.

Prefer a checked-in task runner so contributors do not need to remember tool-specific invocations.

## Coding Style & Naming Conventions

No language or formatter is configured. When code is added, enforce it with a checked-in formatter and linter, and document the canonical command. Use UTF-8, LF line endings, and the selected language's standard indentation (for example, four spaces in Python and two in TypeScript). Use `snake_case` for Python functions and variables, `camelCase` for JavaScript/TypeScript values, and `PascalCase` for types and components.

## Testing Guidelines

No test framework or coverage threshold exists yet. Once selected, name tests by behavior, place them under `tests/` unless the framework requires co-location, and add regression tests for every bug fix. Run the full suite before opening a pull request; record any required coverage target in the tooling configuration.

## Commit & Pull Request Guidelines

There is no Git history, so no commit convention can be inferred. After initializing Git, use concise imperative subjects such as `Add HS300 data loader`; consider Conventional Commits if the project adopts them. Keep commits focused. Pull requests should summarize the change, list verification commands, link relevant issues, and include screenshots only for UI changes. Never commit credentials, proprietary market data, or local environment files.
