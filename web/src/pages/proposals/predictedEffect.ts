/**
 * The copy layer for a proposal's predicted effect (LOOP-06).
 *
 * 05-UI-SPEC's copywriting contract fixes these sentences character for
 * character, including the two places the numbers are interpolated. Keeping
 * them here (rather than inline in the card) means the contract is assertable
 * by unit test through a real render and can never drift field by field.
 *
 * The predicted effect is prose, never a chart, a gauge or a progress bar —
 * the same "calmer under Operate mode" precedent as the Dashboard funnel.
 */
import type { PredictedEffect } from "./types"

/**
 * The shape of one sentence of effect copy: the rendered sentence plus the
 * substrings that carry the number, so a caller may emphasise the headline
 * fact without re-parsing the string.
 */
export interface EffectCopy {
  text: string
  emphasis: string[]
}

/** The honest answer when the payload is absent or a kind we don't know. */
const NO_EFFECT_COPY = "No predicted effect available."

/**
 * Human labels for the nine dotted paths in `PERMITTED_EDIT_FIELDS`. An
 * explicit map, not a regex: `posting_age_days` and `compensation_floor` would
 * both humanise into something wrong ("Posting age days" reads as a duration,
 * not a limit) and the map is the one place those names are decided.
 */
const FIELD_LABELS: Record<string, string> = {
  seniority_min: "Minimum seniority level",
  seniority_max: "Maximum seniority level",
  posting_age_days: "Posting age limit",
  locations: "Location criteria",
  compensation_floor: "Compensation floor",
  "exclusions.title_keywords": "Excluded title keywords",
  "exclusions.employers": "Excluded employers",
  work_authorization: "Work authorization",
  dimension_weights: "Dimension weights",
}

function titleCase(value: string): string {
  if (value.length === 0) return value
  return value.charAt(0).toUpperCase() + value.slice(1)
}

/**
 * The human name of an edit field. Known permitted fields get their decided
 * label; anything else (historical or hand-edited data) degrades to the leaf
 * name with underscores opened out, never to a blank or a raw dotted path.
 */
export function formatFieldLabel(field: string): string {
  const known = FIELD_LABELS[field]
  if (known !== undefined) return known
  const leaf = field.split(".").pop() ?? field
  const spaced = leaf.replaceAll("_", " ").trim()
  return spaced.length === 0 ? field : titleCase(spaced)
}

/** The date portion of "You rejected a similar proposal on {date}". */
export function formatRejectionDate(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(date)
}

/**
 * The one sentence describing what a proposed change would do to the backlog.
 *
 * Tiers A/B carry the exact counts the user is consenting to; Tier C is honest
 * that the field is not enforced yet (RESEARCH Pitfall 1) and is therefore
 * informational; Tier D reports the backlog-wide score delta.
 */
export function describePredictedEffect(effect: PredictedEffect | null): string {
  if (effect === null) return NO_EFFECT_COPY

  switch (effect.kind) {
    case "filter_dry_run": {
      if (effect.would_exclude > 0) {
        return `${effect.would_exclude} of your current listings would now be excluded`
      }
      if (effect.would_include > 0) {
        return `${effect.would_include} of your current listings would newly be included`
      }
      return NO_EFFECT_COPY
    }
    case "flag_recompute":
      return `${effect.would_flag} would newly be flagged ${effect.flag}`
    case "literal_count": {
      const label = formatFieldLabel(effect.field)
      return `${effect.matches} listings mention this — ${label} isn't enforced by discovery yet, so this is informational only.`
    }
    case "score_recompute": {
      const magnitude = Math.abs(effect.mean_delta).toFixed(1)
      const sign = effect.mean_delta < 0 ? "-" : "+"
      return `Backlog-wide score change: ${effect.affected} listings affected, average ${sign}${magnitude}`
    }
    default: {
      // Exhaustiveness guard: a fifth payload kind fails `tsc` here rather
      // than silently rendering nothing. At runtime an unrecognised payload
      // from a newer server degrades to the honest no-effect sentence.
      const _never: never = effect
      void _never
      return NO_EFFECT_COPY
    }
  }
}
