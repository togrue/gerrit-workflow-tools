// Scorecards of several walks side by side (test/walk/README.md): per loop and per step, the user's
// inputs, context switches, first response, settled, requests, git processes, Gerrit requests and
// the steps over budget. Steps are matched by loop and position, so a step the walk renamed (the UI
// led there another way) still lines up with the one it replaced; both titles are shown.
//
//   node out/test/walk/compare.js "baseline=<dir>" "new UI=<dir>" … > comparison.md

import * as fs from "node:fs";
import * as path from "node:path";

import { LoopTotals, Scorecard, StepScore } from "./scorecard";

interface Run {
  label: string;
  card: Scorecard;
}

const cell = (value: number | null | undefined, unit = "") => (value === null || value === undefined ? "–" : `${value}${unit}`);

/** `a → b (±d)` across the runs: the first value and each next one with its difference from the first. */
function across(values: (number | null | undefined)[], unit = ""): string[] {
  const [first] = values;
  return values.map((value, index) => {
    if (index === 0 || value === null || value === undefined || first === null || first === undefined) {
      return cell(value, unit);
    }
    const d = value - first;
    return `${cell(value, unit)} (${d > 0 ? "+" : ""}${d})`;
  });
}

const METRICS: [string, (t: LoopTotals) => number, string][] = [
  ["Steps", (t) => t.steps, ""],
  ["Inputs (clicks + keys + typed)", (t) => t.user_actions, ""],
  ["  clicks", (t) => t.clicks, ""],
  ["  keys", (t) => t.keys, ""],
  ["  typed texts", (t) => t.typed, ""],
  ["Context switches", (t) => t.switches, ""],
  ["Settled (sum)", (t) => t.settled_ms, " ms"],
  ["Requests to ger serve", (t) => t.requests, ""],
  ["git processes", (t) => t.subprocesses, ""],
  ["Gerrit requests", (t) => t.gerrit, ""],
  ["Windows estimate (sum)", (t) => t.windows_ms, " ms"],
  ["Steps over budget", (t) => t.over, ""],
];

function firstSum(card: Scorecard, loop: string): number {
  return card.steps.filter((s) => s.loop === loop).reduce((sum, s) => sum + (s.first_ms ?? 0), 0);
}

export function compare(runs: Run[]): string {
  const lines = ["# Walks compared", "", `Runs: ${runs.map((r) => `**${r.label}**`).join(", ")}. A difference is against the first run.`, ""];
  for (const loop of ["review", "rework"]) {
    const totals = runs.map((r) => r.card.loops.find((t) => t.loop === loop));
    if (totals.every((t) => !t)) {
      continue;
    }
    lines.push(`## ${loop === "review" ? "Review loop" : "Rework loop"}`, "");
    lines.push(`| | ${runs.map((r) => r.label).join(" | ")} |`, `|---|${runs.map(() => "---").join("|")}|`);
    for (const [name, pick, unit] of METRICS) {
      lines.push(`| ${name} | ${across(totals.map((t) => (t ? pick(t) : null)), unit).join(" | ")} |`);
    }
    lines.push(`| First response (sum) | ${across(runs.map((r) => firstSum(r.card, loop)), " ms").join(" | ")} |`, "");
    const count = Math.max(...runs.map((r) => r.card.steps.filter((s) => s.loop === loop).length));
    lines.push("### Per step", "");
    lines.push("Each cell: inputs · switches · first · settled · requests · git · Gerrit; **over** when over budget.", "");
    lines.push(`| # | ${runs.map((r) => r.label).join(" | ")} |`, `|---|${runs.map(() => "---").join("|")}|`);
    for (let i = 0; i < count; i++) {
      const steps = runs.map((r) => r.card.steps.filter((s) => s.loop === loop)[i] as StepScore | undefined);
      const cells = steps.map((s) =>
        s
          ? `${s.title}${s.input === "real" ? "" : ` (${s.input})`}: ${s.user_actions} · ${s.switches} · ${cell(s.first_ms, " ms")} · ${cell(s.settled_ms, " ms")} · ${s.requests} · ${s.subprocesses} · ${s.gerrit}${s.over.length ? " **over**" : ""}`
          : "–",
      );
      lines.push(`| ${i + 1} | ${cells.join(" | ")} |`);
    }
    lines.push("");
  }
  return `${lines.join("\n")}\n`;
}

if (require.main === module) {
  const runs = process.argv.slice(2).map((arg): Run => {
    const [label, dir] = arg.includes("=") ? [arg.slice(0, arg.indexOf("=")), arg.slice(arg.indexOf("=") + 1)] : [path.basename(arg), arg];
    return { label, card: JSON.parse(fs.readFileSync(path.join(dir, "scorecard.json"), "utf8")) as Scorecard };
  });
  process.stdout.write(compare(runs));
}
