// Pictures of the gallery's frames, taken by a Chromium browser that is installed anyway (Edge,
// Chrome), run headless: no dependency, nothing to download. One process per picture.

import { execFile } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

/** Names the browser to use, for where none is found or another one is wanted. */
export const BROWSER_ENV = "GER_GALLERY_BROWSER";

/** Pixels per CSS pixel in a picture: small text and 16px icons stay readable. */
const SCALE = 2;
/** For measuring as for the pictures: text wraps at slightly other places at another scale. */
const SCALED = `--force-device-scale-factor=${SCALE}`;

/** Where a Chromium browser is usually installed on `platform`, the likeliest first. */
export function browserCandidates(platform: NodeJS.Platform, env: NodeJS.ProcessEnv): string[] {
  if (platform === "win32") {
    const roots = [env["ProgramFiles(x86)"], env.ProgramFiles, env.LOCALAPPDATA].filter((dir): dir is string => Boolean(dir));
    const apps = ["Microsoft\\Edge\\Application\\msedge.exe", "Google\\Chrome\\Application\\chrome.exe"];
    return apps.flatMap((app) => roots.map((dir) => path.win32.join(dir, app)));
  }
  if (platform === "darwin") {
    return ["Google Chrome", "Microsoft Edge", "Chromium"].map((app) => `/Applications/${app}.app/Contents/MacOS/${app}`);
  }
  const names = ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge"];
  const dirs = (env.PATH ?? "").split(":").filter(Boolean);
  return names.flatMap((name) => dirs.map((dir) => path.posix.join(dir, name)));
}

/** The browser to run: the one the environment names (and only that one), else the first installed. */
export function findBrowser(platform: NodeJS.Platform, env: NodeJS.ProcessEnv, exists: (file: string) => boolean): string | undefined {
  const named = env[BROWSER_ENV];
  return (named ? [named] : browserCandidates(platform, env)).find(exists);
}

const newProfile = () => fs.mkdtempSync(path.join(os.tmpdir(), "ger-gallery-"));
const dropProfile = (profile: string) => fs.rmSync(profile, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });

/**
 * Run the browser headless on `url` and give what it printed. `profile` is a throwaway one, so a
 * browser you have open is left alone. Edge on Windows hands over to a second process and exits
 * at once, before anything is printed or written, unless told not to; other browsers ignore that.
 */
function headless(browser: string, profile: string, args: string[], url: string): Promise<string> {
  const all = ["--headless", "--edge-skip-compat-layer-relaunch", "--disable-gpu", "--hide-scrollbars", "--no-first-run", "--no-default-browser-check", `--user-data-dir=${profile}`, ...args, url];
  return new Promise<string>((resolve, reject) => {
    execFile(browser, all, { timeout: 120_000, maxBuffer: 64 * 1024 * 1024 }, (error, stdout) => (error ? reject(error) : resolve(stdout)));
  });
}

/**
 * How tall each frame of the gallery page drew, by the `data-shot` of its iframe: the page gives
 * every frame its content's height (index.js), and the browser prints the page once it settled.
 */
export async function frameHeights(browser: string, indexUrl: string): Promise<Map<string, number>> {
  const profile = newProfile();
  const dom = await headless(browser, profile, ["--dump-dom", "--virtual-time-budget=20000", "--window-size=1700,1200", SCALED], indexUrl).finally(() => dropProfile(profile));
  const heights = new Map<string, number>();
  for (const [tag] of dom.matchAll(/<iframe\b[^>]*>/g)) {
    const [shot, height] = [/data-shot="([^"]+)"/.exec(tag)?.[1], /data-height="(\d+)"/.exec(tag)?.[1]];
    if (shot && height) {
      heights.set(shot, Number(height));
    }
  }
  return heights;
}

/** A picture to take: the top left `width` × `height` CSS pixels of the document at `url`, into `file`. */
export interface Shot {
  url: string;
  file: string;
  width: number;
  height: number;
}

/** Take every picture, `atOnce` browsers at a time; each of them keeps one profile for all its pictures, which starts faster than a new one. */
export async function takePictures(browser: string, shots: readonly Shot[], atOnce: number): Promise<void> {
  const queue = [...shots];
  const worker = async () => {
    const profile = newProfile();
    try {
      for (let shot = queue.shift(); shot; shot = queue.shift()) {
        fs.rmSync(shot.file, { force: true });
        await headless(browser, profile, [`--screenshot=${shot.file}`, `--window-size=${shot.width},${shot.height}`, SCALED, "--virtual-time-budget=5000"], shot.url);
        if (!fs.existsSync(shot.file)) {
          throw new Error(`the browser wrote no picture of ${shot.url}`);
        }
      }
    } finally {
      dropProfile(profile);
    }
  };
  await Promise.all(Array.from({ length: Math.min(atOnce, shots.length) }, worker));
}
