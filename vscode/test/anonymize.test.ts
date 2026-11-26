// The anonymizer (src/anonymize.ts) is what stands between a secret project and a bug report. So:
// every field of the view models has a decision; nothing that comes from the repository or Gerrit
// is in what it returns; what it returns depends on the shape of what went in and on nothing else;
// and the report still reads as the views did (lengths, lines, who and which file is which).

import assert from "node:assert/strict";
import { test } from "node:test";

import { ICONS, anonymize } from "../src/anonymize";
import { CommentChain, MoveConflict } from "../src/api";
import { ChangeVm, changeVm } from "../src/changeRows";
import { Random } from "../src/fakes";
import { TreeRow, reportText } from "../src/report";
import { readSnapshot } from "../src/snapshot";
import { StackVm, stackVm } from "../src/stackRows";
import { chain, comment, commit, content, diff, finding, never, shown, stack, state } from "./fixtures";
import { scenarios } from "./gallery/scenarios";

/** The same numbers every run, so a test cannot fail by chance. */
function seeded(seed: number): Random {
  let at = seed;
  return (below) => {
    at = (at + 0x6d2b79f5) | 0;
    let t = Math.imul(at ^ (at >>> 15), 1 | at);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return Math.floor((((t ^ (t >>> 14)) >>> 0) / 4294967296) * below);
  };
}

/** Every value that is not a list or an object, by where it is: `rows[].badges[].title`. */
function leaves(value: unknown, path = ""): [string, unknown][] {
  if (Array.isArray(value)) {
    return value.flatMap((item) => leaves(item, `${path}[]`));
  }
  if (typeof value === "object" && value !== null) {
    return Object.entries(value).flatMap(([key, item]) => (item === undefined ? [] : leaves(item, path ? `${path}.${key}` : key)));
  }
  return [[path, value]];
}

/** Review chains as the end-to-end suite's fake Gerrit fills it, with `word` in every subject and name. */
function tree(word: string, number: (n: number) => number): TreeRow[] {
  const row = (label: string, description: string, icon: string | null, contextValue: string | null = null, children: TreeRow[] = []): TreeRow => ({ label, description, icon, contextValue, children });
  return [
    row(`CI failed on ${number(2)}: ${word} temporary experiment`, "", "target"),
    row("Outgoing", "1 chain · 1 with failed CI · 1 with comments · oldest first", null, null, [
      row(`${word} cleanup after experiment`, "● new activity · 2 changes · V-1 CR+2 💬2 · active 3d ago", "error", "ger.chain.url.local", [
        row(`${word} cleanup after experiment`, `${number(3)} · V-1 CR+2 · in your stack`, "error", "ger.change.url.local"),
        row(`${word} temporary experiment`, `${number(2)} · no votes`, "clock", "ger.change.url"),
      ]),
    ]),
    row("To review", "1 chain", null, null, [row(`${word} retry in the uploader`, `${word}alice · 1 change · V+1 · waiting 12m · active 4h ago`, "eye", "ger.change.url")]),
    row("Nothing needs you right now", "", "pass"),
  ];
}

/**
 * A stack, a change and the tree in which everything that comes from the repository or from Gerrit
 * has `word` in it, every commit id is made from `hex`, and every change and line number starts
 * with `digit`. Two calls with other secrets of the same lengths have the same shape.
 */
function secretViews(word: string, hex: string, digit: number) {
  const sha = (n: number) => (hex + n.toString(16).padStart(2, "0")).repeat(5);
  const number = (n: number) => digit * 10000 + 4210 + n;
  const line = (n: number) => digit * 1000 + 120 + n;
  const people = { alice: `${word}Alice ${word}Arnold`, bob: `${word}bob` };
  const paths = { tree: `${word}src/${word}widget/${word}tree.${word}py`, init: `${word}src/${word}widget/__${word}__.py`, top: `${word}Makefile`, old: `${word}src/${word}old/${word}tree.${word}py` };
  const pushed = (n: number, summary: string, extra: Parameters<typeof commit>[1] = {}) =>
    commit(`${word} ${summary}`, {
      sha: sha(n),
      short_sha: sha(n).slice(0, 7),
      change_id: `I${sha(n + 100)}`,
      number: number(n),
      gerrit_url: `https://gerrit.${word}.example/c/${word}/+/${number(n)}`,
      reviewers: [
        { name: people.alice, cc: false },
        { name: people.bob, cc: true },
      ],
      ...extra,
    });
  const commits = [
    pushed(1, "add the table", { code_review: 2, verified: 1 }),
    pushed(2, "route by prefix", { code_review: -1, comments_unresolved: 3, ci_failures: [`${word}-verify`], attention_reasons: ["ci-failed", "unresolved-comments", `${word}-reason`] }),
    pushed(3, "cache the routes\twith a tab", { is_head: true, change_id: null, pushed: false, patchset_status: "absent", number: null, gerrit_url: null, attention_reasons: ["missing-change-id"] }),
  ];
  const conflict: MoveConflict = { sha: sha(2), summary: commits[1].summary, files: [paths.tree, paths.top] };
  const local = stack(commits, { branch: `${word}-branch`, stack: { project: `${word}project`, target_branch: `origin/${word}-main`, push_branch: `${word}-main` } });
  const stackView = stackVm(local, undefined, (lower, upper) => (lower === sha(1) && upper === sha(2) ? conflict : undefined));
  const noUpstream = stackVm(
    { error: { code: "GIT", message: `fatal: no upstream for '${word}-branch'\nin /home/${word}/repo` }, needs_upstream: true },
    { branch: `${word}-branch`, upstream: null, suggested: `origin/${word}-main`, why: `the ${word} branch nearest to it`, candidates: [`origin/${word}-main`] },
    never,
  );
  const rebasing = stackVm(
    stack(
      commits.map((c, index) => ({ ...c, rebase: (["done", "current", "todo"] as const)[index] })),
      { branch: null, rebase_in_progress: true, rebase: { branch: `${word}-branch`, onto: sha(9), orig_head: sha(3), stop: "conflict", applied: false, current: sha(2), unmerged: [paths.tree, paths.top], as_before: false, actions: ["abort", "skip"], refused: { continue: "the index has unresolved conflicts" } } },
    ),
    undefined,
    never,
  );
  const on = (path: string, n: number | null) => ({ path, line: n === null ? null : line(n) });
  const chains: CommentChain[] = [
    chain(`${word}-root-1`, [comment(`${word} should be per segment.\nSecond line of ${word}.`, { ...on(paths.tree, 1), author: people.alice }), comment(`${word} agreed`, { ...on(paths.tree, 1), author: people.bob })], on(paths.tree, 1)),
    chain(`${word}-root-2`, [comment(`${word} about all of it`, { ...on("/PATCHSET_LEVEL", null), author: people.alice }), comment(`${word} done`, { state: "draft", author: undefined, comment_id: `${word}-d1` })], on("/PATCHSET_LEVEL", null)),
    chain(`${word}-root-3`, [comment(`${word} a note of mine`, { ...on(paths.init, 2), state: "note", author: undefined, comment_id: `${word}-n1` })], on(paths.init, 2)),
  ];
  const files = [
    { path: paths.top, status: "M", old_path: null, insertions: 1, deletions: 1 },
    { path: paths.tree, status: "R", old_path: paths.old, insertions: 184, deletions: 37 },
    { path: paths.init, status: "A", old_path: null, insertions: null, deletions: null },
  ];
  const change = changeVm(
    state({
      shown: shown({ ...commits[1], ci_links: [{ label: `${word}-verify`, url: `https://ci.${word}.example/1`, source: "message" }] }),
      chains,
      base: diff({ message: `${commits[1].summary}\n\nBecause of ${word}.\n\nBug: ${word.toUpperCase()}-${number(9)}\nChange-Id: I${sha(102)}`, author: `${people.alice} <${word}@${word}.example>`, files }),
      ci: {
        state: "loaded",
        introduced: new Set(["e1"]),
        contents: [
          content(true, [finding(`${word}_test: AssertionError in ${word}`, "error", "e1", { tool: `${word}lint`, path: paths.tree, line: line(3), detail: `${word} traceback\n  at ${paths.tree}` }), finding(`unused ${word}`, "warning", "w1", { tool: `${word}lint`, path: null, line: null })], {
            url: `https://ci.${word}.example/job/${word}/7/console`,
            build: `${word}-verify #7`,
            truncated: true,
          }),
          content(false, [], { url: `https://ci.${word}.example/job/${word}/8/console`, build: `${word}-lint #8`, error: `HTTP 403 from ${word}` }),
        ],
      },
    }),
  );
  return { stack: stackView, noUpstream, rebasing, change, chains: tree(word, number), secrets: { sha, number, line } };
}

const FIRST = () => secretViews("zqxv", "a3f9c1", 7);
const SECOND = () => secretViews("wkjy", "5be207", 6);

// --- Every field has a decision --------------------------------------------------------

test("every field of the view models is classified: a new one fails here until it is kept or replaced on purpose", () => {
  for (const scenario of scenarios()) {
    assert.deepEqual(anonymize({ stack: scenario.stack, change: scenario.change, chains: null }).unclassified, [], `${scenario.name}: say in SHAPES (src/anonymize.ts) what becomes of each`);
  }
  const { stack, noUpstream, change, chains } = FIRST();
  assert.deepEqual(anonymize({ stack, change, chains }).unclassified, []);
  assert.deepEqual(anonymize({ stack: noUpstream, change: { problem: "x" }, chains: [] }).unclassified, []);
  assert.deepEqual(anonymize({ stack: FIRST().rebasing, change: { problem: "x" }, chains: [] }).unclassified, []);
});

test("a stopped rebase keeps its banner and buttons; each conflicted file's row names the file as its path is replaced", () => {
  const out = anonymize({ stack: FIRST().rebasing, change: null, chains: null }, seeded(5)).stack as StackVm;
  assert.doesNotMatch(JSON.stringify(out), /zqxv|a3f9c1/);
  assert.equal(out.info[0].text, "A rebase stopped on a conflict");
  assert.deepEqual(out.info[0].actions?.map((action) => [action.label, action.command.id]), [["Abort", "ger.rebase.abort"], ["Skip", "ger.rebase.skip"]]);
  assert.deepEqual(out.info.slice(1).map((info) => [info.text === info.conflict, info.detail, info.tooltip]), [
    [true, "conflicted", "Open it, with its conflict markers, to resolve it (Accept Current / Incoming, or Resolve in Merge Editor), then stage it (git add)."],
    [true, "conflicted", "Open it, with its conflict markers, to resolve it (Accept Current / Incoming, or Resolve in Merge Editor), then stage it (git add)."],
  ]);
  assert.deepEqual(out.rows.map((row) => [row.marker, row.dim]), [[null, true], ["conflict", false], [null, false]]);
});

test("the Change view of a commit stopped on a conflict keeps its words and buttons; the file in its headline becomes what the file's row becomes", () => {
  const secret = "zqxvsrc/zqxvd.zqxvtxt";
  const one = changeVm(state({ stopped: { stop: "conflict", unmerged: [secret], actions: ["abort", "skip"] } }));
  const out = anonymize({ stack: null, change: one, chains: null }, seeded(3));
  assert.deepEqual(out.unclassified, []);
  const change = out.change as ChangeVm;
  assert.doesNotMatch(JSON.stringify(change), /zqxv/);
  assert.equal(change.head?.text, `Conflict in ${change.conflicts?.rows[0].text} — resolve it, then Continue`);
  assert.equal(change.conflicts?.rows[0].text, change.conflicts?.rows[0].conflict);
  assert.deepEqual(change.head?.actions?.map((action) => action.label), ["Abort", "Skip"], "the ways on served");
  assert.equal(change.files?.title, "Files, as before the rebase");
  const many = anonymize({ stack: null, change: changeVm(state({ stopped: { stop: "conflict", unmerged: [secret, "b"], actions: ["abort", "skip"] } })), chains: null }, seeded(3)).change as ChangeVm;
  assert.equal(many.head?.text, "Conflicts in 2 files — resolve them, then Continue");
});

test("a field nobody classified is replaced, key and all, whatever its type", () => {
  const vm = { info: [], rows: [{ summary: "Subject", branch: "release-2.4", pushed: true, parents: 12, owner: { name: "Alice", "src/secret.py": ["x.py"] } }], frozen: null, movable: 1, remote: "origin" };
  const out = anonymize({ stack: vm, change: { empty: "x", byPath: { "src/a.py": 3 } }, chains: null }, seeded(1));
  assert.deepEqual(out.unclassified, ["change.byPath", "stack.remote", "stack.rows[].branch", "stack.rows[].owner", "stack.rows[].parents", "stack.rows[].pushed"]);
  assert.doesNotMatch(JSON.stringify([out.stack, out.change]), /branch|release|origin|remote|parents|owner|Alice|secret|src|\.py|byPath|pushed/);
  const row = (out.stack as { rows: Record<string, unknown>[] }).rows[0];
  assert.deepEqual(Object.keys(row), ["summary", "unclassified1", "unclassified2", "unclassified3", "unclassified4"]);
  assert.equal(String(row.unclassified1).length, "release-2.4".length);
  assert.equal(row.unclassified2, false, "a boolean nobody decided about says nothing");
  assert.ok(typeof row.unclassified3 === "number" && row.unclassified3 !== 12 && row.unclassified3 >= 10 && row.unclassified3 < 100);
  assert.deepEqual(Object.keys(row.unclassified4 as object), ["unclassified1", "unclassified2"]);
});

test("a field that is kept only keeps what it was classified for: a count is not a place for text", () => {
  const out = anonymize({ stack: { info: [], rows: [{ dim: "refs/heads/secret", squash: { a: 1 }, sync: "secret-branch", syncIcon: "secret", notes: ["HEAD", "not ours"] }], frozen: "secret words", movable: "main" }, change: null, chains: null }, seeded(2));
  assert.doesNotMatch(JSON.stringify(out), /secret|main|refs|not ours/);
  assert.deepEqual((out.stack as { rows: { notes: string[] }[] }).rows[0].notes[0], "HEAD");
});

test("what the scenarios show of ours is kept as it is: icons, tones, states, counts, our own sentences", () => {
  const ours = /(^|\.)(icon|tone|kind|sync|syncIcon|syncTitle|marker|state|selected|value|disabled|stale|dim|merge|pinned|squash|movable|added|deleted|open|file|reply|held|resolved|resolving|draft|edit|discard|show|message|plain)$|^stack\.frozen$|waiting|head\.(text|detail)$|change\.notes|modes\.options|files\.(title|summary|note|tooltip)$|comments\.summary$|hint\.text$|loading\.readOnly$|message\.readOnly$|badges\[\]\.text$/;
  let compared = 0;
  for (const scenario of scenarios()) {
    const before = { stack: scenario.stack, change: scenario.change };
    const out = anonymize({ ...before, chains: null }, seeded(3));
    const after = new Map(leaves({ stack: out.stack, change: out.change }).map(([path, value], index) => [`${index} ${path}`, value]));
    for (const [index, [path, value]] of leaves(JSON.parse(JSON.stringify(before))).entries()) {
      if (ours.test(path)) {
        assert.deepEqual(after.get(`${index} ${path}`), value, `${scenario.name}: ${path}`);
        compared += 1;
      }
    }
  }
  assert.ok(compared > 1500, `the scenarios were compared: ${compared}`);
  const used = new Set(scenarios().flatMap((scenario) => leaves(scenario).filter(([path]) => /(icon|syncIcon)$/.test(path)).map(([, value]) => value)));
  assert.deepEqual([...used].filter((icon) => !ICONS.includes(String(icon))), [], "an icon the anonymizer does not know is replaced: add it to ICONS");
});

// --- Nothing of theirs is left -----------------------------------------------------------

test("nothing from the repository or Gerrit is in a report: no word, name, path, id, number or host", () => {
  const { stack, noUpstream, change, chains, secrets } = FIRST();
  for (const views of [
    { stack, change, chains },
    { stack: noUpstream, change: { problem: "ger show failed: zqxv is not in /home/zqxv/repo" } as ChangeVm, chains: [] },
    { stack: FIRST().rebasing, change: null, chains: null },
    { stack: null, change: { empty: "Loading 74211: zqxv subject…" } as ChangeVm, chains: null },
  ]) {
    const text = reportText({ extension: "1.2.3", editor: "1.95.0", os: "linux 6.8.0", state: "ready", events: "live", outOfDate: false, ...views }, seeded(4));
    assert.doesNotMatch(text, /zqxv/i, "the marker every secret string has in it");
    assert.doesNotMatch(text, /a3f9c1/, "a commit id, whole or short");
    for (const n of [1, 2, 3, 9]) {
      assert.doesNotMatch(text, new RegExp(`(?<![0-9a-f])(${secrets.number(n)}|${secrets.line(n)})(?![0-9a-f])`), "a change number or a line number");
      assert.ok(!text.includes(secrets.sha(n).slice(0, 7)) && !text.includes(secrets.sha(n + 100).slice(0, 7)));
    }
    const shown = text.slice(text.indexOf('"chains"'));
    // A URL is https://example.invalid/<n> and nothing else.
    assert.equal(/example(?!\.invalid\/\d+["\\])|https?:\/\/(?!example\.invalid\/\d+["\\])|\.py|@|\/home|origin|PATCHSET_LEVEL|Bug:|Change-Id:|403|traceback|AssertionError/.exec(shown)?.[0], undefined);
  }
});

test("nothing of the gallery's scenarios is left either: their subjects, people, files, branches, hosts, tools and numbers", () => {
  const theirs = /widget|routing|prefix|route|alice|arnold|bob|becker|carol|chen|dana|dietrich|example\.org|release|origin|main\b|pylint|pytest|flake8|makefile|\.py|\.png|change-48|(?<![0-9a-f])(48\d{3}|5512|981|1482|212)(?![0-9a-f])|registr|shim|radix|tie-break|3f9a2c1|fatal|refused/i;
  for (const scenario of scenarios()) {
    const text = reportText({ extension: "1.2.3", editor: "1.95.0", os: "linux 6.8.0", state: "ready", events: null, outOfDate: false, stack: scenario.stack, change: scenario.change, chains: null }, seeded(5));
    assert.equal(theirs.exec(text)?.[0], undefined, scenario.name);
  }
});

test("a report depends on the shape of what the views showed and on nothing else in it", () => {
  // Other words, names, paths, ids and numbers of the same lengths: with the same dice, the very same report.
  const [first, second] = [FIRST(), SECOND()].map(({ stack, change, chains }) => JSON.stringify(anonymize({ stack, change, chains }, seeded(6))));
  assert.equal(first, second);
  const noUpstream = [FIRST(), SECOND()].map((views) => JSON.stringify(anonymize({ stack: views.noUpstream, change: null, chains: null }, seeded(6))));
  assert.equal(noUpstream[0], noUpstream[1]);
});

test("two reports of the same views replace names and ids by different ones", () => {
  const { stack, change, chains } = FIRST();
  const take = () => anonymize({ stack, change, chains }) as unknown as { stack: StackVm; change: ChangeVm };
  const [one, two] = [take(), take()];
  assert.notEqual(one.stack.rows[0].sha, two.stack.rows[0].sha);
  assert.notEqual(one.change.files?.rows[1].path, two.change.files?.rows[1].path);
  assert.notEqual(one.change.message?.author, two.change.message?.author);
});

// --- It still reads as the views did -------------------------------------------------------

test("a report is a snapshot the gallery reads, with the rows, lines and lengths the views had", () => {
  const { stack, change, chains } = FIRST();
  const text = reportText({ extension: "1.2.3", editor: "1.95.0", os: "linux 6.8.0", state: "ready", events: "live", outOfDate: false, stack, change, chains }, seeded(7));
  const read = readSnapshot(text);
  assert.equal(read.name, "Report");
  const before = leaves(JSON.parse(JSON.stringify({ stack, change, chains })));
  const after = leaves({ stack: read.stack, change: read.change, chains: JSON.parse(text).chains });
  assert.deepEqual(after.map(([path]) => path), before.map(([path]) => path), "the same fields, rows and lists");
  // Made up anew rather than filled to length: paths (roughly as long), URLs and ids; a note or a tooltip may have a path in it.
  const reshaped = /\.(path|name|dir|url|id)$|files\.rows\[\]\.tooltip$|threads\[\]\.notes\[\]$|(findings|warnings)\[\]\.notes\[\]$|links\[\]\.tooltip$/;
  let texts = 0;
  for (const [index, [path, value]] of before.entries()) {
    const now = after[index][1];
    assert.equal(typeof now, typeof value, path);
    if (typeof value === "string" && typeof now === "string" && !reshaped.test(path)) {
      assert.deepEqual(now.split("\n").map((line) => line.length), value.split("\n").map((line) => line.length), `${path}: every line as long as it was`);
      texts += 1;
    } else if (typeof value !== "string") {
      assert.equal(now === null, value === null, path);
    }
  }
  assert.ok(texts > 150);
});

test("filler is words of its own: not the original's letters, word lengths or punctuation", () => {
  const original = "Fix: the_router (v2) crashes!\n\n\tsecond line, with a tab";
  const out = anonymize({ stack: null, change: { problem: original }, chains: null }, seeded(8)).change as { problem: string };
  assert.deepEqual(out.problem.split("\n").map((line) => line.length), original.split("\n").map((line) => line.length));
  assert.match(out.problem, /^[a-z ]+\n\n[a-z ]+$/, "letters and blanks only");
  const words = (text: string) => text.split(/\s+/).map((word) => word.length).join(" ");
  assert.notEqual(words(out.problem), words(original));
});

test("a path keeps how many segments it has and roughly how long they are; no name and no extension survives", () => {
  const { change } = FIRST();
  const out = anonymize({ stack: null, change, chains: null }, seeded(9)).change as ChangeVm;
  for (const [index, row] of (out.files?.rows ?? []).entries()) {
    const original = change.files?.rows[index];
    assert.ok(original);
    const [was, now] = [original.path.split("/"), row.path.split("/")];
    assert.equal(now.length, was.length, "as many segments");
    for (const [at, segment] of now.entries()) {
      assert.match(segment, /^[a-z]+$/, "letters only: no dot, so no extension");
      assert.ok(segment.length >= was[at].length && segment.length < was[at].length + 8, "roughly as long, never exactly a tell");
    }
    assert.equal(row.name, now.at(-1));
    assert.equal(row.dir, now.slice(0, -1).join("/"));
  }
  const [, renamed, added] = out.files?.rows ?? [];
  assert.equal(renamed.dir, added.dir, "two files of one folder are in one folder");
  assert.equal(renamed.tooltip.split(" → ")[1], renamed.path);
  assert.equal(renamed.tooltip.split(" → ")[0].split("/").length, 3);
});

test("within one report the same commit, change, person, file and line are the same everywhere", () => {
  const { stack, change, chains } = FIRST();
  const out = anonymize({ stack, change, chains }, seeded(10)) as unknown as { stack: StackVm; change: ChangeVm; chains: TreeRow[] };
  const [head, second, first] = out.stack.rows;
  for (const row of out.stack.rows) {
    assert.match(row.sha, /^[0-9a-f]{40}$/);
    assert.equal(row.shortSha, row.sha.slice(0, 7), "the short id is the start of the whole one");
    assert.ok(row.notes.includes(row.shortSha));
    assert.equal(row.key, row.changeId ?? row.sha);
  }
  assert.match(second.changeId ?? "", /^I[0-9a-f]{40}$/);
  assert.equal(head.changeId, null);
  assert.match(first.url ?? "", /^https:\/\/example\.invalid\/\d+$/);
  assert.equal(head.url, null);
  // The Change view shows the second commit: the same key, and its short id in "Local commit …".
  assert.equal(out.change.key, second.key);
  assert.equal(out.change.message?.key, second.key);
  assert.equal(out.change.versions?.options[0].label, `Local commit ${second.shortSha}`);
  const [, tree, init] = out.change.files?.rows.map((row) => row.path) ?? [];
  const threads = out.change.comments?.threads ?? [];
  // A comment is on that file, by that person.
  assert.match(threads[0].notes[0], new RegExp(`^${out.change.files?.rows[1].name}:\\d{4}$`));
  assert.equal(threads[0].notes[1], threads[0].author);
  assert.equal(threads[1].author, threads[0].author, "one person, one name");
  assert.notEqual(threads[0].replies[0].author, threads[0].author);
  assert.deepEqual(threads[1].notes, ["whole change", threads[1].author]);
  assert.equal(threads[1].replies[0].author, "you");
  assert.deepEqual(threads[2].notes.slice(0, 1), ["your private note"]);
  assert.match(threads[2].notes[1], new RegExp(`^${init.split("/").at(-1)}:\\d{4}$`));
  assert.equal(threads[0].body.split("\n")[0], threads[0].text, "the first line of a comment is its row's text");
  assert.equal(out.change.message?.text.split("\n")[0], second.summary, "the subject is the commit message's first line");
  // A CI finding is in that file, at the line its note says.
  const finding = out.change.ci?.links[0].findings[0];
  assert.equal(finding?.path, tree);
  assert.ok(finding?.line && finding.line >= 1000 && finding.line <= 9999 && finding.line !== 7123);
  assert.ok(finding.notes.includes(`${tree}:${finding.line}`) && finding.notes.includes("introduced here"));
  assert.equal(out.change.ci?.links[0].tooltip.split("\n")[1], "The log was cut: findings may be incomplete.");
  assert.equal(out.change.ci?.links[0].tooltip.split("\n")[0], out.change.ci?.links[0].url);
  // Review chains: the same change number under its chain as in the headline's row, our words around it kept.
  const [, outgoing, toReview, nothing] = out.chains;
  assert.equal(outgoing.description, "1 chain · 1 with failed CI · 1 with comments · oldest first");
  assert.match(outgoing.children[0].description, /^● new activity · 2 changes · V-1 CR\+2 💬2 · active 3d ago$/);
  assert.match(outgoing.children[0].children[0].description, /^\d{5} · V-1 CR\+2 · in your stack$/);
  assert.equal(outgoing.children[0].children[0].label, outgoing.children[0].label, "a chain is named after its top change");
  assert.match(toReview.children[0].description, /^[A-Za-z ]{9} · 1 change · V\+1 · waiting 12m · active 4h ago$/);
  assert.deepEqual([outgoing.label, toReview.label, nothing.label, nothing.icon, toReview.children[0].contextValue], ["Outgoing", "To review", "Nothing needs you right now", "pass", "ger.change.url"]);
});

test("a sentence with their words in it goes whole; a tooltip keeps the lines that are ours", () => {
  const { stack, noUpstream } = FIRST();
  const out = anonymize({ stack, change: null, chains: null }, seeded(11)).stack as StackVm;
  const [, second] = out.rows;
  const lines = second.tooltip.split("\n");
  assert.ok(lines.includes("the same as on Gerrit") === false, "\"Change 74212: the same as on Gerrit\" has the change number in it");
  assert.match(second.tooltip, /^[a-z \n]+$/, "the subject, the number, an unknown reason, the reviewers and the files of a conflict: all filler");
  assert.deepEqual(second.badges.map((badge) => badge?.title.split("\n")[0]), [second.badges[0]?.title.split("\n")[0], "Code-Review -1", second.badges[2]?.title]);
  assert.match(second.badges[0]?.title ?? "", /^3 comment chains unresolved/, "the comment words are ours");
  assert.match(second.badges[2]?.title ?? "", /^[a-z ]+$/, "CI failed: <the builds' names>");
  assert.match(second.badges[1]?.title.split("\n")[1] ?? "", /^[a-z ]+$/, "Reviewers: <names>");
  assert.equal(out.rows[0].tooltip.split("\n")[1], "not on Gerrit");
  const info = (anonymize({ stack: noUpstream, change: null, chains: null }, seeded(12)).stack as StackVm).info;
  assert.match(info[0].text, /^[a-z ]+$/, "\"<branch> has no upstream…\"");
  assert.match(info[1].text, /^[a-z ]+$/, "\"Track <branch>\"");
  assert.equal(info[2].text, "Choose another upstream branch…");
  assert.deepEqual([info[1].command?.id, info[1].command?.args.length, info[1].icon], ["ger.setUpstream", 1, "git-branch"]);
  assert.match(String(info[1].command?.args[0]), /^[a-z ]+$/);
});
