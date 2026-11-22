// The `contextValue` of every tree row. package.json menus match them with `viewItem =~ /…/`
// regexes, so the strings are one scheme, built in one place and checked against the menus by
// test/package.test.ts (which also needs to know every value a row can have). No `vscode` import.
//
//   ger.change|chain [.url] [.local]    a change or a chain in Review chains; .local: it is a commit of your stack
//
// Local stack and Change are webviews: their rows carry a context object instead (media/stack.js,
// `webview/context` in package.json), and their buttons send messages.

export interface ChangeRow {
  kind: "change" | "chain";
  /** Gerrit has a page for it. */
  url: boolean;
  /** A commit of the local stack: its commit is in this checkout. */
  local: boolean;
}

export function changeRowContext({ kind, url, local }: ChangeRow): string {
  return `ger.${kind}${url ? ".url" : ""}${local ? ".local" : ""}`;
}

/** Every contextValue the views can produce. */
export function allRowContexts(): string[] {
  const bools = [false, true];
  const all: string[] = [];
  for (const kind of ["change", "chain"] as const) {
    for (const url of bools) {
      for (const local of bools) {
        all.push(changeRowContext({ kind, url, local }));
      }
    }
  }
  return all;
}

/** The titles of the two sections of Review chains, by the name the service gives them. */
export const SECTION_TITLES: Record<string, string> = { "to-review": "To review", outgoing: "Outgoing" };

/** What a row of Review chains says when it stands for no change. */
export const TREE_WORDS = {
  nothingNeedsYou: "Nothing needs you right now",
  nothingToReview: "Nothing to review",
  noChanges: "No open changes",
};
