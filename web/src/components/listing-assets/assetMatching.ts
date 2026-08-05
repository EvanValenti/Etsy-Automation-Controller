/**
 * Relevance matching for the Listing Assets finder.
 *
 * The Listing ID field is a live search query: an operator types "stop"
 * and should see only the assets belonging to that listing, across every
 * engine. Previously the field filtered nothing -- every job from every
 * engine was always on screen, and the operator had to recognize theirs
 * by eye.
 *
 * Matching is deliberately loose and token-based, because the same
 * listing is named differently by each engine ("Stop. There's Mushrooms"
 * as a product name, "stop_there_s_mushrooms" as a job slug,
 * "mushroom_hunting" as a campaign). Normalizing punctuation away and
 * requiring every query token to appear somewhere in the asset's own
 * searchable text is what lets one query find all three.
 */

/** Lowercase, strip punctuation, collapse whitespace. "Stop. There's
 * Mushrooms" and "stop_there_s_mushrooms" both become "stop there s
 * mushrooms". */
export function normalize(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

export function tokenize(text: string): string[] {
  const normalized = normalize(text);
  return normalized ? normalized.split(" ") : [];
}

/**
 * True when every token of the query appears in at least one of the
 * asset's searchable fields.
 *
 * Per-token (not whole-string) so word order and separators don't matter:
 * "mushrooms stop" finds the same asset as "stop mushrooms". Substring
 * (not exact) so a partial word still matches while typing -- "mush"
 * finds "mushrooms", which is what makes this usable as live search.
 *
 * An empty query matches nothing here; callers decide what an empty
 * query means for their section (this page shows no assets until one is
 * entered, rather than dumping every asset on screen).
 */
export function matchesQuery(fields: (string | null | undefined)[], query: string): boolean {
  const tokens = tokenize(query);
  if (tokens.length === 0) return false;
  const haystack = fields.filter(Boolean).map((f) => normalize(f as string)).join(" ");
  if (!haystack) return false;
  return tokens.every((token) => haystack.includes(token));
}

/**
 * How well an asset matches, for ordering results -- higher is better.
 * Only ever used to sort assets that ALREADY matched; it never promotes
 * an unrelated asset into the results.
 */
export function matchScore(fields: (string | null | undefined)[], query: string): number {
  const normalizedQuery = normalize(query);
  const haystack = fields.filter(Boolean).map((f) => normalize(f as string)).join(" ");
  if (!haystack || !normalizedQuery) return 0;
  // Whole-query substring beats scattered token hits -- an asset whose
  // name literally contains what was typed is the better match.
  if (haystack.includes(normalizedQuery)) return 2;
  return 1;
}
