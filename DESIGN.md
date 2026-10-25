---
version: alpha
name: SIGNIX
description: A precise, map-first climate-tech workspace for rooftop solar planning.
colors:
  solar-amber: "#E59A2F"
  deep-plum: "#2A1733"
  ink: "#1D1A20"
  warm-paper: "#F7F3EA"
  white: "#FFFFFF"
  sage: "#2F6B53"
typography:
  display:
    fontFamily: "Inter, Segoe UI, system-ui, sans-serif"
  body:
    fontFamily: "Inter, Segoe UI, system-ui, sans-serif"
rounded:
  small: "8px"
  medium: "14px"
  large: "22px"
spacing:
  compact: "8px"
  standard: "16px"
  section: "32px"
components:
  button-primary:
    background: "#2A1733"
    color: "#FFFFFF"
    radius: "10px"
  input-location:
    background: "#FFFFFF"
    color: "#1D1A20"
    border: "#D9D1C5"
    radius: "10px"
---

# Design System: SIGNIX

**Status:** Current interface guidance for the active root application. Implementation ownership and validation commands live in [the frontend guide](docs/frontend-guide.md).

## Overview

**Creative North Star: "The Solar Survey Sheet"**

SIGNIX should feel like a well-made field instrument translated into software: editorial enough to establish trust, compact enough to support real work, and warm enough to remain approachable to a homeowner. The map is the working surface, not background decoration. Solar amber is used like a surveyor's mark—sparingly, where attention or energy is relevant.

The system avoids generic dashboard spectacle. Structure comes from typography, rules, and aligned surfaces rather than a collection of floating cards. Data confidence and workflow status are written in plain language.

SIGNIX has two named product variants inside this identity. **SIGNIX Home** uses approachable household language and leads with monthly bill savings, upfront cost, and conditional subsidy guidance. **SIGNIX Business** uses a denser analytical hierarchy and leads with annual net operating benefit, project investment, ROI, payback, and explicit unmodeled assumptions. They share one geometry workspace and component system; track differences encode real workflow and financial-policy differences rather than decorative themes.

**Key Characteristics:**
- Map-first composition with compact editorial guidance.
- Warm neutral ground, deep ink/plum structure, restrained solar amber.
- Fine borders and low ambient elevation instead of glow effects.
- Clear provenance, uncertainty, and recovery messaging.

## Colors

The palette pairs a warm paper field with deep technical ink and one deliberate solar accent.

### Primary
- **Deep Plum:** Primary navigation, high-emphasis actions, and structural headings.

### Secondary
- **Solar Amber:** Focused accents, active survey marks, and energy-related emphasis.

### Tertiary
- **Measured Sage:** Confirmed success and usable-state communication.

### Neutral
- **Working Ink:** Primary copy and high-contrast controls.
- **Warm Paper:** Application ground and quiet grouped regions.
- **White:** Inputs and working surfaces that need maximum clarity.

### Named Rules

**The Survey Mark Rule.** Solar amber should identify a decision or active location state; it is not a decorative wash.

## Typography

**Display Font:** Inter (with Segoe UI and system fallbacks)  
**Body Font:** Inter (with Segoe UI and system fallbacks)

**Character:** A compact grotesk hierarchy keeps the product contemporary and legible. Display copy uses tighter tracking and stronger contrast; operational copy stays calm and readable.

### Hierarchy
- **Display** (750, clamp(2.25rem, 5vw, 4.5rem), 0.98): Concise first-use proposition.
- **Headline** (750, 1.5–2rem, 1.1): Workflow and section orientation.
- **Title** (700, 1rem, 1.3): Controls and grouped content.
- **Body** (400–500, 0.95rem, 1.6): Explanations, limited to readable line lengths.
- **Label** (700, 0.72rem, 0.08em, uppercase): Provenance and workflow metadata.

### Named Rules

**The One Clear Sentence Rule.** The first viewport explains the product and the next action without stacking slogans.

## Layout

The landing experience uses an asymmetric editorial grid: a compact value-and-workflow column beside the location console. The map follows immediately as the primary work surface. Desktop widths preserve information density; tablet and mobile collapse to one column without hiding location actions or map controls.

Authentication presents Home and Business as mutually exclusive product cards. Once authenticated, the sidebar carries a persistent product switch and every dashboard, saved-site list, comparison, survey, and report reflects the active or saved track. Home lists are named “My Solar”; Business lists are named “Business Sites.”

## Elevation & Depth

Surfaces are flat by default. Fine borders and tonal shifts define groups; a low ambient shadow is reserved for the location console and elevated interactive results.

**The Flat-by-Default Rule.** Do not turn every section into a floating card.

## Shapes

Corners are gently technical rather than pill-heavy. Inputs and buttons use medium radii; major work surfaces may use the large radius. Rules, small square markers, and clipped map edges reinforce the survey-sheet character.

## Components

### Buttons
- **Shape:** Compact rounded rectangle (10px), with generous horizontal padding.
- **Primary:** Deep plum fill and white text; solar amber is reserved for a small mark or active state.
- **Hover / Focus:** Modest tonal shift and a visible high-contrast focus ring; no glow.
- **Secondary / Ghost:** White or transparent ground with an explicit border.

### Cards / Containers
- **Corner Style:** Large working surfaces use 22px; compact grouped controls use 14px.
- **Background:** White on warm paper.
- **Shadow Strategy:** Low ambient elevation only where layering is functional.
- **Border:** Fine warm-gray boundary.
- **Internal Padding:** 16–28px depending on surface scale.

### Inputs / Fields
- **Style:** White fill, visible warm-gray stroke, persistent label, and explicit action.
- **Focus:** Deep plum border with a two-pixel warm-amber outer indicator.
- **Error / Disabled:** Plain-language status remains adjacent to the field and does not rely on color alone.
- **Ownership:** Select/listbox controls remain native HTML selects for keyboard, mobile, and bilingual reliability. WTForms owns validation for account forms; estimate inputs use adjacent JavaScript status messaging plus backend validation.

### Navigation
- **Style:** Quiet vertical navigation with authored line icons. The active item uses weight, a left survey mark, and a tonal ground.
- **Product context:** A compact two-option Home/Business switch sits beneath the SIGNIX identity. Solar amber marks the current Home context; measured sage marks Business analytical emphasis in content, without changing primary action semantics.

### Location Console

The signature component groups address search, device location, and Maps-link parsing while keeping them visibly distinct. A live status line and keyboard-operable results list make uncertainty and progress explicit.

## Do's and Don'ts

### Do:
- **Do** keep the selected location, map marker, address, and downstream coordinates synchronized.
- **Do** use warm amber as a rare functional signal.
- **Do** qualify estimates and provider-derived data in nearby copy.

### Don't:
- **Don't** use unsupported savings, payback, accuracy, or eligibility claims.
- **Don't** use emoji as primary interface icons.
- **Don't** hide the map beneath an oversized marketing hero.
- **Don't** silently substitute a guessed location when a provider fails.
