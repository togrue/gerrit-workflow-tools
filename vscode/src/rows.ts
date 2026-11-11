// The `contextValue` of every tree row. package.json menus match them with `viewItem =~ /…/`
// regexes, so the strings are one scheme, built in one place and checked against the menus by
// test/package.test.ts (which also needs to know every value a row can have). No `vscode` import.
//
//   ger.change|chain [.url] [.local]    a change or a chain in Review chains; .local: it is a commit of your stack
//   ger.commit [.url] [.up|.upx] [.down|.downx]   a commit of the local stack; .upx/.downx: that move would conflict
//   ger.thread.open.free|held, ger.thread.resolved, ger.mine   comment rows of the Change view

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

export interface CommitRow {
  url: boolean;
  /** A neighbour above: the commit can move later, unless the swap would conflict. */
  up: "up" | "upx" | undefined;
  down: "down" | "downx" | undefined;
}

export function commitRowContext({ url, up, down }: CommitRow): string {
  return `ger.commit${url ? ".url" : ""}${up ? `.${up}` : ""}${down ? `.${down}` : ""}`;
}

export interface ThreadRow {
  /** Your own draft or private note, not a thread somebody started. */
  mine: boolean;
  resolved: boolean;
  /** Kept for you: the agent leaves it alone. */
  held: boolean;
}

export function threadRowContext({ mine, resolved, held }: ThreadRow): string {
  return mine ? "ger.mine" : resolved ? "ger.thread.resolved" : `ger.thread.open${held ? ".held" : ".free"}`;
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
  for (const url of bools) {
    for (const up of [undefined, "up", "upx"] as const) {
      for (const down of [undefined, "down", "downx"] as const) {
        all.push(commitRowContext({ url, up, down }));
      }
    }
  }
  for (const mine of bools) {
    for (const resolved of bools) {
      for (const held of bools) {
        all.push(threadRowContext({ mine, resolved, held }));
      }
    }
  }
  return [...new Set(all)];
}
