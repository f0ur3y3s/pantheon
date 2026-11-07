---
name: Pantheon
description: Operations-console design implemented in the React dashboard.
colors:
  dark-bg: "#0b0c0e"
  dark-surface: "#111316"
  dark-raised: "#171a1e"
  dark-hover: "#1b1f24"
  dark-line: "#23272d"
  dark-line-strong: "#30353c"
  dark-text: "#e8eaed"
  dark-secondary: "#a3abb5"
  dark-muted: "#8a939e"
  dark-accent: "#5b8def"
  dark-accent-ink: "#0b0c0e"
  dark-accent-soft: "rgba(91, 141, 239, .14)"
  dark-ok: "#3ecf8e"
  dark-ok-soft: "rgba(62, 207, 142, .12)"
  dark-warn: "#f2b13d"
  dark-warn-soft: "rgba(242, 177, 61, .12)"
  dark-bad: "#f26b70"
  dark-bad-soft: "rgba(242, 107, 112, .12)"
  dark-track: "#22262c"
  light-bg: "#f6f7f9"
  light-surface: "#ffffff"
  light-raised: "#f1f3f5"
  light-hover: "#f3f5f8"
  light-line: "#e3e6ea"
  light-line-strong: "#d3d8de"
  light-text: "#15181c"
  light-secondary: "#4f5864"
  light-muted: "#5f6975"
  light-accent: "#2f63d6"
  light-accent-ink: "#ffffff"
  light-accent-soft: "rgba(47, 99, 214, .1)"
  light-ok: "#16865a"
  light-ok-soft: "rgba(22, 134, 90, .1)"
  light-warn: "#a86a09"
  light-warn-soft: "rgba(168, 106, 9, .1)"
  light-bad: "#c4383e"
  light-bad-soft: "rgba(196, 56, 62, .09)"
  light-track: "#e6e9ed"
typography:
  headline:
    fontFamily: "\"Geist\", ui-sans-serif, system-ui, sans-serif"
    fontSize: "22px"
    fontWeight: 600
    letterSpacing: "-.02em"
  title:
    fontFamily: "\"Geist\", ui-sans-serif, system-ui, sans-serif"
    fontSize: "15px"
    fontWeight: 600
    letterSpacing: "-.01em"
  body:
    fontFamily: "\"Geist\", ui-sans-serif, system-ui, sans-serif"
    fontSize: "14px"
    lineHeight: 1.5
  label:
    fontFamily: "\"Geist\", ui-sans-serif, system-ui, sans-serif"
    fontSize: "13px"
    fontWeight: 500
  mono:
    fontFamily: "\"Geist Mono\", ui-monospace, \"Cascadia Code\", monospace"
    fontSize: "12px"
rounded:
  control: "8px"
  panel: "12px"
  compact: "6px"
  sheet: "16px 16px 0 0"
spacing:
  tight: "6px"
  control: "8px"
  compact: "10px"
  field: "12px"
  row: "14px"
  panel: "16px"
  section: "28px"
components:
  button-primary:
    backgroundColor: "{colors.dark-accent}"
    textColor: "{colors.dark-accent-ink}"
    rounded: "{rounded.control}"
    height: "32px"
    padding: "0 12px"
  button-secondary:
    backgroundColor: "{colors.dark-surface}"
    textColor: "{colors.dark-text}"
    rounded: "{rounded.control}"
    height: "32px"
    padding: "0 12px"
  button-ghost:
    textColor: "{colors.dark-secondary}"
    rounded: "{rounded.control}"
    height: "32px"
    padding: "0 12px"
  input:
    backgroundColor: "{colors.dark-bg}"
    textColor: "{colors.dark-text}"
    rounded: "{rounded.control}"
    height: "34px"
    padding: "0 10px"
  panel:
    backgroundColor: "{colors.dark-surface}"
    rounded: "{rounded.panel}"
---
# Design System: Pantheon

## Overview

**Creative North Star: "The Operations Console"**

Pantheon follows a conventional infrastructure console: compact, quiet, and legible. Grafana, Vercel, Tailscale, and Proxmox establish the quality benchmark. Dark is the default, with an explicit light option. Neutral surfaces carry information; blue identifies actions and selection; green, amber, and red communicate state.

This system is implemented in `dashboard/src/`, following the selected prototype at `.impeccable/mocks/pantheon-console.html`. Machine state, operations, logs, configurations, parameters, profiles, and client snippets use the existing gateway APIs. The local simulator verifies these flows without operating real model hosts. Deployment and live-host validation are recorded separately from visual review.

**Key Characteristics:**
- Machine overview with controls beside the selected machine.
- Restrained borders, tonal layering, and one action accent.
- Readable identifiers, visible state labels, and aligned numbers.
- Preserved three-bar Pantheon logo.

## Colors

Near-black charcoal and cool neutrals form the default palette; the light option preserves the same semantic assignments. Frontmatter records the exact source values with `dark-` and `light-` prefixes. Component frontmatter shows dark defaults; light mode uses each corresponding light token.

### Primary

Blue `accent` identifies actions, links, focus, selected tabs, and operation progress. `accent-soft` tints selected rows; `accent-ink` supplies text on filled actions.

### Secondary

Semantic `ok`, `warn`, and `bad` colors indicate serving/success, transition/pressure/warnings, and failure respectively. Their soft counterparts support local state emphasis. These are state signals rather than additional brand accents.

### Neutral

`bg` covers the page and inset fields/code; `surface` contains tables and panels; `raised` distinguishes headers and pressed segments; `hover` marks interactive surfaces. `line` divides content and `line-strong` outlines controls. `text`, `secondary`, and `muted` establish reading hierarchy. `track` is the unfilled meter rail.

**The State Color Rule.** Reserve green, amber, and red for operational meaning and always accompany machine state with text.

## Typography

Geist with system sans-serif fallbacks is the UI face; Geist Mono with platform monospace fallbacks carries model identifiers, endpoints, code, and logs. Numeric cells use tabular figures. The mock loads Geist families from Google Fonts; the dashboard uses the same stylesheet links with system fallbacks.

The frontmatter maps the compact page heading, panel heading, body, label, and mono roles. Supporting text and table headers use 12px. Summary figures use 20px at weight 500; the selected-machine heading uses 17px at weight 600. Logs have 1.65 line height and snippets 1.6. No display or marketing scale is established.

## Layout

The centered desktop page has a maximum width of 1400px, 24px horizontal gutters, 28px top padding, and 64px bottom padding. The sticky top bar is 56px high. A four-cell summary strip precedes the machine table; routing and activity panels form a two-column grid below.

Machine selection adds a 420px right column with a 28px gap. Its panel sticks 84px from the top and caps its height at the viewport minus 108px. The table drops the Requests column while the panel displays that detail. Long identifiers truncate with full values available for inspection.

Between 1081px and 1280px the selected-machine column narrows to 380px with a 20px gap and its lower panels stack. At widths up to 1080px the machine panel becomes a fixed bottom sheet, capped at 88dvh above a scrim. At widths up to 760px horizontal gutters become 16px, summaries become two columns, lower panels stack, and table rows become compact records. Detail-panel actions reach a minimum height of 36px and icon controls reach 40px. Breadcrumb and endpoint leave the header; requests and empty memory cells leave compact rows. The React implementation preserves this responsive behavior; local browser checks cover desktop, intermediate, and phone widths.

## Elevation & Depth

Most surfaces are flat, separated by single-pixel borders and tonal changes. The selected-machine panel uses the theme-specific shadow recorded in the sidecar. The sticky bar mixes the page background at 88% with transparency and applies a 10px backdrop blur. A translucent black scrim separates the bottom sheet. Small soft halos emphasize status dots rather than general container elevation.

## Shapes

Controls and inset code areas use the control radius; tables, summary strips, and panels use the panel radius. Compact icon buttons and segments use the compact radius. The bottom sheet rounds only its top corners. The endpoint capsule is pill-shaped; the static prototype also uses a mock-data pill. State dots are circular, with an outlined offline variant. Preserve all three slanted bars of the existing Pantheon mark and their differing opacities.

## Components

### Buttons

Compact, bordered, and text-led. Default buttons use surface fill and a strong border; primary buttons use accent fill and accent ink. Ghost buttons remove the border and use secondary text. Destructive actions use failure-colored text. Standard buttons are 32px high with 12px horizontal padding; small buttons are 28px high with 10px padding. Hover changes the surface; primary hover mixes accent with text. Disabled buttons use 0.45 opacity and an unavailable cursor. Shared keyboard focus is a 2px accent outline with a 2px offset.

### Inputs / Fields

Inset fields use page background, strong borders, control corners, and a 34px height. Labels and hints remain visible. Hover strengthens the border; edited parameters receive an accent border and a small blue marker with an accessible edited label. Native selects share this treatment. Configuration errors use failure-colored supporting text.

### Navigation

Overview, Parameters, Logs, and Connect stay within the selected machine panel. Secondary text becomes primary text on hover; a 2px accent underline identifies selection. The client selector uses bordered segments and a raised fill for its pressed option. Icon controls have accessible names. Machine tabs support arrow, Home, and End keys; the compact sheet traps focus, makes the background inert, closes with Escape, and restores its opener. Full assistive-technology testing remains outstanding.

### Tables and status

Raised headers and thin dividers organize the bordered table. Cells use 14px by 16px padding; hover shades rows and selection adds the soft accent fill. State is a dot plus text; unavailable telemetry is a dash. Memory uses a 6px rail plus a percentage, with warning fill above 90%. Detail-panel meter values stay on one line with automatic width. In-flight operations add a step label and blue progress bar beneath the model text. Progress comes from polled operations; unavailable values remain explicit rather than being simulated.

### Panels and code

Bordered panels use restrained padding and divided headers. The machine panel groups controls, parameters, logs, and scoped client configuration. Logs and snippets use inset backgrounds, mono text, borders, and scrolling. Empty logs explain their state directly. Configuration-copy buttons briefly show Copied or Copy failed; icon copy controls update their accessible status and title.

Observed motion uses 150ms control/row transitions and a 350ms panel entrance with the extracted easing. Busy dots pulse over 1.4s. Meter values update directly through width styles without width transitions; live log lines render without the prototype-only fresh-line highlight. Reduced-motion preferences disable all animation and transitions. Motion and shadows belong in sidecar extensions.

## Do's and Don'ts

### Do:
- Do verify actual API behavior separately from static visual fidelity.
- Do pair state color with a readable label and keep offline machines visible.
- Do preserve the three-bar logo and existing API request and response contracts.
- Do retain full identifiers for inspection and copying when their display truncates.
- Do preserve visible keyboard focus and reduced-motion support.

### Don't:
- Don't treat simulated progress, metrics, activity, logs, or client snippets as live production evidence.
- Don't use state colors as decorative accents.
- Don't replace the selected table-and-machine-panel direction with a page of machine cards.
- Don't invent token scales, logo replacements, or unsupported component variants.

Memory telemetry in the fleet table shows separate RAM and VRAM lines whenever reported, each with used/total capacity and a percentage meter. Missing telemetry is not substituted with a different memory type.

Client selectors group options at the left edge at every width. Actions that stop, switch, or restart a model, save parameter replacements, reset overrides, or sign out require a themed modal confirmation. Cancel receives initial focus; Escape cancels without closing the parent panel; focus returns to the invoking control.
