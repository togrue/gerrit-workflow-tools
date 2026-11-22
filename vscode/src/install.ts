// Installing ger from the extension: which installer is there, what it is asked to install, and
// where its result lands. Pure: no `vscode` import, unit-tested.

import * as path from "node:path";

/** The ger release with this extension's version: the two are built and released together. */
export function releaseUrl(repository: string, version: string): string {
  return `${repository.replace(/(\.git)?\/*$/, "")}/releases/download/v${version}/ger.tar.gz`;
}

/** The full path of `program` on the `PATH` in `env`, as the platform would find it. */
export function onPath(program: string, env: NodeJS.ProcessEnv, platform: string, isFile: (candidate: string) => boolean): string | undefined {
  const windows = platform === "win32";
  const separator = windows ? ";" : ":";
  const join = windows ? path.win32.join : path.posix.join;
  const extensions = windows ? (env.PATHEXT ?? ".COM;.EXE;.BAT;.CMD").split(";").filter(Boolean) : [""];
  for (const dir of (env.PATH ?? env.Path ?? "").split(separator).filter(Boolean)) {
    for (const extension of extensions) {
      const candidate = join(dir, program + extension);
      if (isFile(candidate)) {
        return candidate;
      }
    }
  }
  return undefined;
}

/**
 * The command that installs ger from `url`, with the installer that is there: uv, else pipx. Both
 * give ger an environment of its own, which plain pip refuses to do on many systems.
 */
export function installArgv(url: string, has: (program: string) => boolean): string[] | undefined {
  if (has("uv")) {
    return ["uv", "tool", "install", "--force", url];
  }
  return has("pipx") ? ["pipx", "install", "--force", url] : undefined;
}

/**
 * Where uv and pipx put `ger` (`~/.local/bin`). An editor started before that directory came onto
 * the `PATH` does not see it there, so a fresh install is looked up here too.
 */
export function installedGer(home: string, platform: string, isFile: (candidate: string) => boolean): string | undefined {
  const candidate = platform === "win32" ? path.win32.join(home, ".local", "bin", "ger.exe") : path.posix.join(home, ".local", "bin", "ger");
  return isFile(candidate) ? candidate : undefined;
}
