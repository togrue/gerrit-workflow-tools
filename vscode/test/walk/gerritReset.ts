// After a walk: undo on Gerrit what it did as the user, so the next run starts where this one did.
// The votes it cast are removed, the reviewed changes put back in the user's attention set, and
// the drafts it created deleted (one it only changed, a Reply Done on your own earlier draft, stays
// as the walk left it). A comment it published stays (only an administrator can delete one). Uses
// the checkout's own ger.webUrl, ger.user and ger.token.

import { spawnSync } from "node:child_process";

const config = (repo: string, key: string) => spawnSync("git", ["config", "--get", key], { cwd: repo, encoding: "utf8" }).stdout.trim();

function gerrit(repo: string): ((method: string, path: string, body?: unknown) => Promise<unknown>) | undefined {
  const base = config(repo, "ger.webUrl").replace(/\/$/, "");
  const user = config(repo, "ger.user");
  const token = config(repo, "ger.token");
  if (!base || !user || !token) {
    return undefined;
  }
  const auth = `Basic ${Buffer.from(`${user}:${token}`).toString("base64")}`;
  const call = async (method: string, path: string, body?: unknown): Promise<unknown> => {
    const headers: Record<string, string> = body === undefined ? { Authorization: auth } : { Authorization: auth, "Content-Type": "application/json" };
    const response = await fetch(`${base}/a/${path}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
    const text = await response.text();
    if (!response.ok) {
      throw new Error(`${method} ${path}: HTTP ${response.status} ${text.slice(0, 200)}`);
    }
    return text.startsWith(")]}'") ? JSON.parse(text.slice(4)) : undefined;
  };
  return call;
}

type Drafts = Record<string, { id: string; patch_set: number }[]>;

/** The ids of every draft the user has now: what a reset must not delete. */
export async function draftIds(repo: string): Promise<Set<string>> {
  const call = gerrit(repo);
  const ids = new Set<string>();
  if (!call) {
    return ids;
  }
  const changes = ((await call("GET", "changes/?q=has:draft&n=100")) as { _number: number }[] | undefined) ?? [];
  for (const change of changes) {
    const drafts = (await call("GET", `changes/${change._number}/drafts`)) as Drafts | undefined;
    Object.values(drafts ?? {})
      .flat()
      .forEach((draft) => ids.add(draft.id));
  }
  return ids;
}

export async function resetGerrit(repo: string, since: Date, before: Set<string>, changes: number[], log: (line: string) => void): Promise<void> {
  const call = gerrit(repo);
  const user = config(repo, "ger.user");
  if (!call) {
    log("reset: no ger.webUrl, ger.user or ger.token: nothing undone");
    return;
  }
  for (const change of changes) {
    try {
      const info = (await call("GET", `changes/${change}/detail`)) as {
        messages?: { author?: { username?: string }; date: string; message: string }[];
        labels?: Record<string, { all?: { username?: string; value?: number }[] }>;
      };
      const ours = (info.messages ?? []).filter((m) => m.author?.username === user && new Date(`${m.date.replace(" ", "T")}Z`) >= since);
      if (ours.some((m) => /Code-Review[+-]\d/.test(m.message))) {
        await call("DELETE", `changes/${change}/reviewers/self/votes/Code-Review`, { notify: "NONE" });
        await call("POST", `changes/${change}/attention`, { user, reason: "the scorecard walk is reset", notify: "NONE" });
        log(`reset: ${change}: the walk's Code-Review vote removed, back in ${user}'s attention set`);
      }
      const drafts = (await call("GET", `changes/${change}/drafts`)) as Drafts | undefined;
      for (const draft of Object.values(drafts ?? {}).flat()) {
        if (!before.has(draft.id)) {
          await call("DELETE", `changes/${change}/revisions/${draft.patch_set}/drafts/${draft.id}`);
          log(`reset: ${change}: the walk's draft ${draft.id} deleted`);
        }
      }
    } catch (error) {
      log(`reset: ${change}: ${error instanceof Error ? error.message : String(error)}`);
    }
  }
}
