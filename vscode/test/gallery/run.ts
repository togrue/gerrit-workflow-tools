// npm run gallery [-- [--png <dir>] [--only <words>] [snapshot.json …]]
//
// Writes the gallery to out/gallery and prints the path of its page. Snapshot files (a user's
// report) are added as scenarios, in front of the built-in ones. With --png, a picture of every
// frame at every width goes into <dir>, so layout can be looked at without a browser window.
// --only keeps the scenarios whose name has the words in it: fewer pictures, sooner.

import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { pathToFileURL } from "node:url";

import { readSnapshot } from "../../src/snapshot";
import { BROWSER_ENV, findBrowser, frameHeights, takePictures } from "./browser";
import { WIDTHS, frames, writeGallery } from "./build";
import { Scenario, scenarios } from "./scenarios";

const OUT = path.join(__dirname, "..", "..", "gallery");
/** Browsers at work at once while taking pictures. */
const AT_ONCE = Math.max(2, Math.min(8, os.availableParallelism() - 1));
/** A view with next to nothing in it still shows as a strip of the side bar. */
const MIN_HEIGHT = 44;
/** A frame that did not say how tall it is still gets a picture. */
const UNKNOWN_HEIGHT = 600;

const USAGE = "usage: npm run gallery -- [--png <dir>] [--only <words>] [snapshot.json …]";

interface Args {
  png: string | undefined;
  only: string;
  files: string[];
}

function parse(argv: string[]): Args {
  const args: Args = { png: undefined, only: "", files: [] };
  for (let index = 0; index < argv.length; index++) {
    if (argv[index] === "--png") {
      const dir = argv[++index];
      if (!dir) {
        throw new Error("--png needs the folder the pictures go to");
      }
      args.png = path.resolve(dir);
    } else if (argv[index] === "--only") {
      args.only = (argv[++index] ?? "").toLowerCase();
    } else if (argv[index].startsWith("--")) {
      throw new Error(`unknown option ${argv[index]}\n${USAGE}`);
    } else {
      args.files.push(argv[index]);
    }
  }
  return args;
}

function fromFile(file: string): Scenario {
  try {
    return readSnapshot(fs.readFileSync(file, "utf8"));
  } catch (error) {
    throw new Error(`${file}: ${error instanceof Error ? error.message : String(error)}`);
  }
}

/** One picture per frame and width into `dir`; the number written. */
async function pictures(browser: string, index: string, all: readonly Scenario[], dir: string): Promise<number> {
  fs.mkdirSync(dir, { recursive: true });
  const heights = await frameHeights(browser, pathToFileURL(index).href);
  const shots = frames(all).flatMap((frame) => WIDTHS.map((width) => ({ frame, width, name: `${frame.id}.${width}` })));
  const unmeasured = shots.filter((shot) => !heights.has(shot.name));
  if (unmeasured.length) {
    console.error(`${unmeasured.length} of ${shots.length} frames did not say how tall they are (the first: ${unmeasured[0].name}); their pictures are ${UNKNOWN_HEIGHT}px high.`);
  }
  // Each frame alone in shot.html, which gives it the side bar's width whatever the browser's window is.
  const shot = pathToFileURL(path.join(path.dirname(index), "shot.html")).href;
  const sized = shots.map(({ frame, width, name }) => {
    const height = Math.max(heights.get(name) ?? UNKNOWN_HEIGHT, MIN_HEIGHT);
    return { url: `${shot}?frame=${encodeURIComponent(frame.file)}&width=${width}&height=${height}`, file: path.join(dir, `${name}.png`), width, height };
  });
  await takePictures(browser, sized, AT_ONCE);
  return shots.length;
}

async function main(): Promise<number> {
  const args = parse(process.argv.slice(2));
  const all = [...args.files.map(fromFile), ...scenarios()].filter((scenario) => scenario.name.toLowerCase().includes(args.only));
  if (!all.length) {
    throw new Error(`no scenario has "${args.only}" in its name`);
  }
  const index = writeGallery(OUT, all);
  console.log(`${all.length} scenarios, ${frames(all).length * WIDTHS.length} frames: ${index}`);
  if (!args.png) {
    return 0;
  }
  const browser = findBrowser(process.platform, process.env, fs.existsSync);
  if (!browser) {
    const named = process.env[BROWSER_ENV];
    console.error(named ? `--png: ${BROWSER_ENV} names ${named}, which is not there.` : `--png: no Edge, Chrome or Chromium was found. Set ${BROWSER_ENV} to the path of a Chromium browser.`);
    return 1;
  }
  const count = await pictures(browser, index, all, args.png);
  console.log(`${count} pictures (<scenario>.<view>.<theme>.<width>.png): ${args.png}`);
  return 0;
}

main().then(
  (code) => {
    process.exitCode = code;
  },
  (error) => {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 2;
  },
);
