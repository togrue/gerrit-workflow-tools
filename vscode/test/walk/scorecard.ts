// The scorecard of one walk (test/walk/README.md): from its trace, per step the user's input
// (clicks, keys, typed text), the context switches, the time to the first visible response and
// to settled, the requests and the subprocesses; totals per loop; the steps over budget; and a
// Windows estimate, where each subprocess costs `processMs` more than here. Pure: no vscode.

export type Loop = "review" | "rework";
/**
 * `walk`: moves within data already loaded (next comment, next file): something shows within 100 ms.
 * `change`: changes what is shown (another change, a draft saved): settled within 1 s.
 * `slow`: allowed to take longer (a rebase in the terminal, publishing): progress shows within 100 ms.
 */
export type Budget = "walk" | "change" | "slow";

export const BUDGET_MS = { first: 100, settled: 1000 };

export interface StepRecord {
  kind: "step";
  loop: Loop;
  n: number;
  title: string;
  budget: Budget;
  t: number;
  /** When the step's outcome showed (the walk's own check), from its first input; null when it did not. */
  reached_ms: number | null;
  /** When the extension was idle again after it. */
  idle_ms: number;
  /** When its last input was given (several clicks and keys, typing): settled counts from there. */
  input_ms?: number;
  clicks: number;
  keys: number;
  typed: number;
  switches: number;
  where: string[];
  /** `real`: clicks and keys given through the window; `command`: the commands they run; `mixed`. */
  input: "real" | "command" | "mixed";
  given: string[];
  fallbacks?: string[];
  error?: string;
  picture: string;
}

type Rec = { kind: string; t?: number } & Record<string, unknown>;

interface ActionRec extends Rec {
  id: string;
  name: string;
  first_ms: number | null;
  first: string | null;
  settled_ms: number;
}

interface ServeRec extends Rec {
  rid: number;
  ms: number;
  git: { count: number; ms: number };
  gerrit: { count: number; ms: number };
  calls: [string, string, number][];
}

interface RequestRec extends Rec {
  rid: number;
  method: string;
  path: string;
  ms: number;
}

/** A ger command run in a terminal, while GER_TRACE was set there. */
interface CliRec extends Rec {
  argv: string[];
  ms: number;
  git: { count: number; ms: number };
  gerrit: { count: number; ms: number };
  calls: [string, string, number][];
}

export interface StepScore {
  loop: Loop;
  n: number;
  title: string;
  budget: Budget;
  input: StepRecord["input"];
  given: string[];
  fallbacks: string[];
  user_actions: number;
  clicks: number;
  keys: number;
  typed: number;
  switches: number;
  first_ms: number | null;
  first: string | null;
  settled_ms: number | null;
  requests: number;
  request_ms: number;
  subprocesses: number;
  gerrit: number;
  windows_ms: number | null;
  over: string[];
  /** The slowest requests and terminal commands of the step, and their slowest calls: why it took what it took. */
  causes: string[];
  actions: string[];
  error?: string;
  picture: string;
}

export interface LoopTotals {
  loop: Loop;
  steps: number;
  user_actions: number;
  clicks: number;
  keys: number;
  typed: number;
  switches: number;
  settled_ms: number;
  windows_ms: number;
  requests: number;
  subprocesses: number;
  gerrit: number;
  over: number;
}

export interface Scorecard {
  format: 1;
  process_ms: number;
  budgets: typeof BUDGET_MS;
  steps: StepScore[];
  loops: LoopTotals[];
  notes: string[];
}

export function readTrace(text: string): Rec[] {
  return text
    .split("\n")
    .filter((line) => line.trim())
    .map((line) => JSON.parse(line) as Rec);
}

const ms = (value: number) => Math.round(value);

export function scorecard(records: Rec[], options: { processMs: number }): Scorecard {
  const steps = records.filter((r): r is StepRecord & Rec => r.kind === "step");
  const starts = records.filter((r) => r.kind === "step-start");
  const actions = records.filter((r): r is ActionRec => r.kind === "action");
  const requests = records.filter((r): r is RequestRec => r.kind === "request");
  const serves = new Map(records.filter((r): r is ServeRec => r.kind === "serve").map((r) => [r.rid, r]));
  const clis = records.filter((r): r is CliRec => r.kind === "cli");
  const scores = steps.map((step) => {
    const from = step.t - 1;
    const to = step.t + step.idle_ms;
    const inside = (r: Rec) => typeof r.t === "number" && r.t >= from && r.t <= to;
    const own = actions.filter(inside);
    const asked = requests.filter(inside);
    const ran = clis.filter(inside);
    const served = asked.map((request) => serves.get(request.rid)).filter((serve): serve is ServeRec => serve !== undefined);
    const firsts = own.filter((a) => a.first_ms !== null).map((a) => ({ at: (a.t as number) + (a.first_ms as number) - step.t, what: a.first }));
    // The step's outcome showing is a response too: the only one a step the workbench answers alone (a tree row expanding) has.
    if (step.reached_ms !== null) {
      firsts.push({ at: step.reached_ms, what: "outcome (seen by the walk)" });
    }
    firsts.sort((a, b) => a.at - b.at);
    const settledByActions = Math.max(0, ...own.map((a) => (a.t as number) + a.settled_ms - step.t));
    const settled = step.reached_ms === null ? null : ms(Math.max(step.reached_ms, settledByActions) - (step.input_ms ?? 0));
    const subprocesses = served.reduce((sum, s) => sum + s.git.count, 0) + ran.reduce((sum, c) => sum + c.git.count, 0);
    const gerrit = served.reduce((sum, s) => sum + s.gerrit.count, 0) + ran.reduce((sum, c) => sum + c.gerrit.count, 0);
    const first = firsts[0];
    const firstMs = first ? ms(first.at) : null;
    const windowsMs = settled === null ? null : ms(settled + subprocesses * options.processMs);
    const over: string[] = [];
    if (step.error) {
      over.push(step.error);
    }
    if (step.budget === "walk" && (firstMs === null || firstMs > BUDGET_MS.first)) {
      over.push(firstMs === null ? "nothing showed" : `first response ${firstMs} ms > ${BUDGET_MS.first} ms`);
    }
    if (step.budget === "change" && settled !== null && settled > BUDGET_MS.settled) {
      over.push(`settled ${settled} ms > ${BUDGET_MS.settled} ms`);
    }
    if (step.budget !== "walk" && settled !== null && settled > BUDGET_MS.settled && (firstMs === null || firstMs > BUDGET_MS.first)) {
      over.push(firstMs === null ? "no progress shown" : `progress after ${firstMs} ms > ${BUDGET_MS.first} ms`);
    }
    if (windowsMs !== null && settled !== null && step.budget === "change" && settled <= BUDGET_MS.settled && windowsMs > BUDGET_MS.settled) {
      over.push(`on Windows ~${windowsMs} ms > ${BUDGET_MS.settled} ms (${subprocesses} subprocesses)`);
    }
    if (step.budget === "walk" && subprocesses > 0 && firstMs !== null && firstMs <= BUDGET_MS.first && firstMs + subprocesses * options.processMs > BUDGET_MS.first) {
      over.push(`on Windows up to ~${firstMs + subprocesses * options.processMs} ms to first response (${subprocesses} subprocesses)`);
    }
    const score: StepScore = {
      loop: step.loop,
      n: step.n,
      title: step.title,
      budget: step.budget,
      input: step.input,
      given: step.given,
      fallbacks: step.fallbacks ?? [],
      user_actions: step.clicks + step.keys + step.typed,
      clicks: step.clicks,
      keys: step.keys,
      typed: step.typed,
      switches: step.switches,
      first_ms: firstMs,
      first: first?.what ?? null,
      settled_ms: settled,
      requests: asked.length,
      request_ms: ms(asked.reduce((sum, r) => sum + r.ms, 0)),
      subprocesses,
      gerrit,
      windows_ms: windowsMs,
      over,
      causes: causes(asked, serves, ran),
      actions: own.map((a) => `${a.name}: first ${a.first_ms ?? "-"} ms (${a.first ?? "nothing"}), settled ${a.settled_ms} ms`),
      ...(step.error ? { error: step.error } : {}),
      picture: step.picture,
    };
    return score;
  });
  const loops = (["review", "rework"] as Loop[])
    .map((loop): LoopTotals => {
      const own = scores.filter((s) => s.loop === loop);
      const sum = (pick: (s: StepScore) => number | null) => own.reduce((total, s) => total + (pick(s) ?? 0), 0);
      return {
        loop,
        steps: own.length,
        user_actions: sum((s) => s.user_actions),
        clicks: sum((s) => s.clicks),
        keys: sum((s) => s.keys),
        typed: sum((s) => s.typed),
        switches: sum((s) => s.switches),
        settled_ms: sum((s) => s.settled_ms),
        windows_ms: sum((s) => s.windows_ms),
        requests: sum((s) => s.requests),
        subprocesses: sum((s) => s.subprocesses),
        gerrit: sum((s) => s.gerrit),
        over: own.filter((s) => s.over.length).length,
      };
    })
    .filter((totals) => totals.steps > 0);
  const notes = records.filter((r) => r.kind === "note").map((r) => String(r.text));
  if (starts.length !== steps.length) {
    notes.push(`${starts.length - steps.length} step(s) started and never finished: the walk stopped there`);
  }
  return { format: 1, process_ms: options.processMs, budgets: BUDGET_MS, steps: scores, loops, notes };
}

function causes(asked: RequestRec[], serves: Map<number, ServeRec>, ran: CliRec[]): string[] {
  const lines: { ms: number; text: string }[] = [];
  for (const request of asked) {
    const serve = serves.get(request.rid);
    const calls = serve ? slowest(serve.calls) : "";
    const cost = serve ? ` (serve ${ms(serve.ms)} ms: ${serve.git.count} git, ${serve.gerrit.count} Gerrit${calls ? `; ${calls}` : ""})` : "";
    lines.push({ ms: request.ms, text: `${request.method} ${request.path} ${ms(request.ms)} ms${cost}` });
  }
  for (const cli of ran) {
    lines.push({ ms: cli.ms, text: `terminal: ger ${cli.argv.join(" ")} ${ms(cli.ms)} ms (${cli.git.count} git, ${cli.gerrit.count} Gerrit; ${slowest(cli.calls)})` });
  }
  return lines
    .sort((a, b) => b.ms - a.ms)
    .slice(0, 4)
    .map((line) => line.text);
}

function slowest(calls: [string, string, number][]): string {
  return [...calls]
    .sort((a, b) => b[2] - a[2])
    .slice(0, 3)
    .map(([, label, took]) => `${label.length > 70 ? `${label.slice(0, 70)}…` : label} ${ms(took)} ms`)
    .join(", ");
}

const cell = (value: number | null, unit = "") => (value === null ? "–" : `${value}${unit}`);
const escape = (text: string) => text.replace(/\|/g, "\\|");

export function renderScorecard(card: Scorecard): string {
  const lines = [
    "# Walk scorecard",
    "",
    `Budgets: first response ≤ ${card.budgets.first} ms for a step within loaded data (*walk*); settled ≤ ${card.budgets.settled} ms for one that changes the change (*change*); anything slower shows progress within ${card.budgets.first} ms (*slow*). First response counts from the step's first input, settled from its last. Windows: settled + subprocesses × ${card.process_ms} ms.`,
    "",
  ];
  for (const totals of card.loops) {
    lines.push(`## ${totals.loop === "review" ? "Review loop" : "Rework loop"}`, "");
    lines.push("| # | Step | Budget | Input | Actions (clicks/keys/typed) | Switches | First | Settled | Requests | Subprocesses | Gerrit | Windows | |");
    lines.push("|---|------|--------|-------|-----|-----|------|---------|----------|------|-----|---------|--|");
    for (const s of card.steps.filter((step) => step.loop === totals.loop)) {
      lines.push(
        `| ${s.n} | ${escape(s.title)} | ${s.budget} | ${s.input} | ${s.user_actions} (${s.clicks}/${s.keys}/${s.typed}) | ${s.switches} | ${cell(s.first_ms, " ms")} | ${cell(s.settled_ms, " ms")} | ${s.requests} | ${s.subprocesses} | ${s.gerrit} | ${cell(s.windows_ms, " ms")} | ${s.over.length ? "**over**" : ""} |`,
      );
    }
    lines.push(
      `| | **Total** | | | ${totals.user_actions} (${totals.clicks}/${totals.keys}/${totals.typed}) | ${totals.switches} | | ${totals.settled_ms} ms | ${totals.requests} | ${totals.subprocesses} | ${totals.gerrit} | ${totals.windows_ms} ms | ${totals.over} over |`,
      "",
    );
  }
  const over = card.steps.filter((s) => s.over.length);
  lines.push("## Over budget", "");
  lines.push(...(over.length ? over.map((s) => `- **${s.loop} ${s.n}. ${escape(s.title)}**: ${s.over.join("; ")}`) : ["None."]), "");
  const slowest = [...card.steps].filter((s) => s.settled_ms !== null).sort((a, b) => (b.settled_ms ?? 0) - (a.settled_ms ?? 0)).slice(0, 5);
  lines.push("## Five slowest steps", "");
  for (const s of slowest) {
    lines.push(`- **${s.loop} ${s.n}. ${escape(s.title)}**: settled ${s.settled_ms} ms, first ${cell(s.first_ms, " ms")} (${s.first ?? "nothing"}), Windows ~${s.windows_ms} ms`);
    lines.push(...s.causes.map((c) => `  - ${escape(c)}`));
  }
  lines.push("", "## Steps", "");
  for (const s of card.steps) {
    lines.push(`### ${s.loop} ${s.n}. ${escape(s.title)}`, "");
    lines.push(`- input (${s.input}): ${s.given.map(escape).join(", ") || "none"}${s.fallbacks.length ? `; commands instead: ${s.fallbacks.map(escape).join(", ")}` : ""}`);
    lines.push(`- actions: ${s.actions.map(escape).join("; ") || "none traced"}`);
    if (s.causes.length) {
      lines.push(`- requests: ${s.causes.map(escape).join("; ")}`);
    }
    lines.push(`- picture: \`steps/${s.picture}\``, "");
  }
  if (card.notes.length) {
    lines.push("## Notes", "", ...card.notes.map((note) => `- ${escape(note)}`), "");
  }
  return `${lines.join("\n")}\n`;
}
