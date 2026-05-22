# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.1] - 2026-05-22

### Added

- Added `.codemapy/` artifact generation with `report.html`, `context.json`, `summary.md`, `hubs.json`, and `manifest.json`.
- Added CLI overwrite confirmation for existing `.codemapy/` artifacts, plus `--yes` for non-interactive rewrites.
- Added GUI artifact generation so selecting a project writes `.codemapy/` directly into that project.
- Added dependency graph support for Verilog and SystemVerilog files (`.v`, `.vh`, `.sv`, `.svh`).
- Added dependency graph support for C/C++ include relationships (`.c`, `.h`, `.cc`, `.cpp`, `.cxx`, `.hpp`).
- Added dependency graph support for Rust modules and internal `use` paths (`.rs`).
- Added Svelte support for scanning and JavaScript/TypeScript dependency resolution.
- Added root and nested `.gitignore` support when scanning projects, including directory rules, globs, anchored paths, and negated patterns.
- Added README documentation for supported languages and agent artifacts.

### Changed

- Improved JavaScript and TypeScript relative import resolution, including parent-directory imports and source mappings for `.js` imports that resolve to `.ts` or `.tsx` files.
- Improved Verilog module resolution to prefer source files over generated primitive or implementation outputs.
- Updated the GUI workflow to remove the separate HTML save location and focus on project-level artifact generation.
- Excluded `.codemapy/` from future scans.
- Excluded common dependency, config, and project metadata files from the HTML source map while preserving them in agent artifacts.
- Ignored generated outputs, caches, vendor folders, editor folders, platform build artifacts, and language build artifacts for Python, C/C++, Verilog/SystemVerilog, JavaScript/TypeScript, Svelte, Rust, Go, and Apple projects.

### Fixed

- Suppressed scanned-project Python `SyntaxWarning` messages during import extraction.
- Fixed dependency graph resolution for nested include-style paths across supported languages.

[0.1.1]: https://github.com/animagr/codemapy/compare/v0.1.0...v0.1.1
