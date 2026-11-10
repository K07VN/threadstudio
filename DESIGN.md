# DESIGN.md — Threads Studio Pro

## Surface & Design System
- **Mode**: `Operate` (Impeccable Craft Floor compliant).
- **Component & Token Engine**: `fluent-blue` (`0.2.0`) on `PyQt6` via `fluent_blue.compatibility.qt`.
- **Theme Default**: `dark` (`ThemeManager.set_theme("dark")`), runtime-switchable to `light` and `high-contrast` with zero widget recreation via `ThemedMixin`.

## Architectural & Anti-Slop Rules
1. **No Raw Stylesheets or Hardcoded Hex**: Every custom surface (`InteractiveStageCanvas`) reads colors, radii, fonts, and spacing exclusively from `ThemeManager` inside `paintEvent`.
2. **Flat Container Hierarchy (No Cards-in-Cards)**: Structural regions use `Splitter`, `Panel`, and `Divider` — never nested `Card` inside `Card`.
3. **No Modal Interruption for Core Tasks**: Reviewing and editing scraped comments happens in the dedicated **Duyệt Kịch Bản** split inspector right beside the `9:16` stage so background jobs keep running uninterrupted.
4. **Typography & Numerals**: Single system sans stack (`Segoe UI`) via `ThemeManager.font()` roles (`heading-3`, `heading-5`, `body-medium`, `label`, `caption`, `monospace` for timecodes/coordinates/logs). Maximum 4 active type roles per view.
5. **Three-Zone Spatial Shell**:
   - **Left Rail**: Collapsible `Sidebar` (`220px` / `56px`) with live queue & pending-review `Badge` counters.
   - **Center Workspace**: Task-focused pages (`Batch Queue DataTable`, `Inline Script Inspector`, `Voice Pool`, `Media Presets`, `Settings`).
   - **Right Stage (`380px`–`460px`)**: Persistent `9:16` interactive layout canvas + `VideoPlayer` output preview, anchored above a collapsible multi-worker `LogViewer`.
