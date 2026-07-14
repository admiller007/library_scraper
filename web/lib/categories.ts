// Groups the ~60 event sources into a few human categories so the filter can
// offer "select all Park Districts" etc. Category is derived from the source's
// display name (the DB `library` value); OVERRIDES covers anything the name
// heuristic would get wrong. Adding a normally-named source needs no change here.

export type SourceCategory =
  | 'Libraries'
  | 'Park Districts'
  | 'Museums & Nature'
  | 'Villages & Community';

// Render order for the grouped filter.
export const SOURCE_CATEGORIES: SourceCategory[] = [
  'Libraries',
  'Park Districts',
  'Museums & Nature',
  'Villages & Community',
];

const OVERRIDES: Record<string, SourceCategory> = {
  // Community events feed, not a library.
  'Chicago Events': 'Villages & Community',
};

export function categorize(library: string): SourceCategory {
  const override = OVERRIDES[library];
  if (override) return override;

  const n = library.toLowerCase();
  if (n.includes('park district') || n.includes('parks & rec') || n.endsWith(' parks')) {
    return 'Park Districts';
  }
  if (n.startsWith('village of') || n.endsWith(' city')) {
    return 'Villages & Community';
  }
  if (['museum', 'botanic', 'garden', 'nature', 'preserve'].some((w) => n.includes(w))) {
    return 'Museums & Nature';
  }
  return 'Libraries';
}
