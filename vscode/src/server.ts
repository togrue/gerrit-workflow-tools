// Starts `ger serve` for one repository and hands out a client once it says where it listens.
// No `vscode` import: see api.ts.

import { ChildProcess, spawn } from "node:child_process";

import { GerClient, Health, Hello } from "./api";
import { shellCommandLine, spawnable } from "./commandLine";
import { REQUIRED_API, apiProblem } from "./compat";

const STARTUP_TIMEOUT_MS = 20_000;

/** The program `ger.executable` names is not there. */
export class ExecutableNotFound extends Error {
  constructor(readonly program: string) {
    super(`there is no program "${program}": install ger, or set ger.executable to how to run it (its full path works too)`);
    this.name = "ExecutableNotFound";
  }
}

/** `ger serve` did not come up. `code` is its exit code's name when it said why (`GIT`: not in a git checkout). */
export class ServeFailed extends Error {
  constructor(
    message: string,
    readonly code?: string,
    /** A word for a cause the user can fix, as `ger serve` names it (`insecure-http`). */
    readonly reason?: string,
  ) {
    super(message);
    this.name = "ServeFailed";
  }
}

export class GerProcess {
  private child: ChildProcess | undefined;
  private starting: Promise<GerClient> | undefined;
  /** The exit code of a service that died under the views, while it is started again: the restart is one log line. */
  private died: number | null | undefined;
  /** What ger serve printed on stderr before: a restart says it again, and it is not logged twice. */
  private readonly said = new Set<string>();
  /**
   * Called when a service that was serving exits without being asked to (killed, crashed): the
   * model starts it again at once, so a failed restart is said while the views are watched, not
   * at the next click. Not called for `stop()`.
   */
  onDied: (code: number | null) => void = () => undefined;

  constructor(
    private readonly cwd: string,
    /** The program and leading arguments that run ger, from `ger.executable`. */
    private readonly program: () => string[],
    private readonly log: (line: string) => void,
    /** Told what answered, once per start, when its API fits: the package versions are compared there. */
    private readonly onServed: (health: Health) => void = () => undefined,
  ) {}

  /** The client of the running service; starts the service when it is not running. */
  client(): Promise<GerClient> {
    if (!this.starting) {
      this.starting = this.start().catch((error) => {
        this.starting = undefined;
        throw error;
      });
    }
    return this.starting;
  }

  private start(): Promise<GerClient> {
    // The service stops when its stdin closes, so it cannot outlive the editor.
    const argv = [...this.program(), "serve", "--exit-with-stdin"];
    const shown = shellCommandLine(argv, process.platform === "win32" ? "cmd" : "posix");
    // Started again after it died under the views: one line, once it listens (a failed restart is said in full).
    const again = this.died !== undefined ? { code: this.died } : undefined;
    this.died = undefined;
    if (!again) {
      this.log(`starting: ${shown}  (in ${this.cwd})`);
    }
    return new Promise<GerClient>((resolve, reject) => {
      const { command, args, shell } = spawnable(argv);
      const child = spawn(command, args, { cwd: this.cwd, shell, windowsHide: true, stdio: ["pipe", "pipe", "pipe"] });
      this.child = child;
      let out = "";
      let err = "";
      let settled = false;
      let quietUntil = again ? Infinity : 0;
      const fail = (reason: Error) => {
        if (!settled) {
          settled = true;
          clearTimeout(timer);
          reject(reason);
        }
      };
      const timer = setTimeout(() => {
        fail(new ServeFailed(`ger serve did not start within ${STARTUP_TIMEOUT_MS / 1000}s`));
        this.stop();
      }, STARTUP_TIMEOUT_MS);

      child.stdout?.setEncoding("utf8").on("data", (chunk: string) => {
        if (settled) {
          return;
        }
        out += chunk;
        const end = out.indexOf("\n");
        if (end < 0) {
          return;
        }
        // One JSON line: where it listens, or why it cannot serve.
        let first: (Hello & { error?: undefined }) | { error: { code: string; message: string; reason?: string } };
        try {
          first = JSON.parse(out.slice(0, end));
        } catch {
          fail(new ServeFailed(`ger serve printed something unexpected: ${out.slice(0, end)}`));
          return;
        }
        if (first.error) {
          fail(new ServeFailed(first.error.message, first.error.code, first.error.reason));
          return;
        }
        settled = true;
        clearTimeout(timer);
        this.log(again ? `ger serve exited (${again.code ?? "killed"}) and was started again: listening on ${first.url}` : `ger serve is listening on ${first.url} for ${first.repo}`);
        quietUntil = again ? Date.now() + 5000 : 0;
        resolve(this.checked(new GerClient(first), Boolean(again)));
      });
      child.stderr?.setEncoding("utf8").on("data", (chunk: string) => {
        err += chunk;
        // While it starts again (and a moment after), what it said at its first start is not said twice.
        // A configuration warning, which ger prints with each request, is said once a session.
        const fresh = chunk.trimEnd().split("\n").filter((line) => !this.said.has(line) || (Date.now() >= quietUntil && !line.startsWith("warning: ")));
        chunk.trimEnd().split("\n").forEach((line) => this.said.add(line));
        if (fresh.length) {
          this.log(fresh.join("\n"));
        }
      });
      child.on("error", (error: NodeJS.ErrnoException) =>
        fail(error.code === "ENOENT" ? new ExecutableNotFound(argv[0]) : new ServeFailed(`could not run ${shown}: ${error.message}`)),
      );
      child.on("exit", (code) => {
        const died = this.child === child && settled;
        if (!died) {
          this.log(`ger serve exited (${code ?? "killed"})`);
        }
        if (this.child === child) {
          this.child = undefined;
          this.starting = undefined;
        }
        fail(new ServeFailed(err.trim() || `${shown} exited with code ${code}`));
        if (died) {
          this.died = code;
          this.onDied(code);
        }
      });
    });
  }

  /** `client` once its API is one this extension can use; else the service stops and the reason is the error. */
  private async checked(client: GerClient, again = false): Promise<GerClient> {
    let health: Health;
    try {
      health = await client.health();
    } catch (error) {
      this.stop();
      throw error;
    }
    const problem = apiProblem(health.api, REQUIRED_API);
    if (!again || problem) {
      this.log(`ger ${health.version} speaks API ${health.api}; the extension needs ${REQUIRED_API}: ${problem ? "refused" : "compatible"}`);
    }
    if (problem) {
      this.log(problem.message);
      this.stop();
      throw problem;
    }
    client.served = health;
    if (!again) {
      this.onServed(health); // the same ger: its versions and warnings were said when it first started
    }
    return client;
  }

  /** Stop the service; the next `client()` starts a new one. */
  stop(): void {
    const child = this.child;
    this.child = undefined;
    this.starting = undefined;
    if (child) {
      child.stdin?.end();
      child.kill();
    }
  }
}
