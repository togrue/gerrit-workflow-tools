// Starts `ger serve` for one repository and hands out a client once it says where it listens.
// No `vscode` import: see api.ts.

import { ChildProcess, spawn } from "node:child_process";

import { GerClient, Hello } from "./api";

const STARTUP_TIMEOUT_MS = 20_000;

export class GerProcess {
  private child: ChildProcess | undefined;
  private starting: Promise<GerClient> | undefined;

  constructor(
    private readonly cwd: string,
    private readonly executable: () => string,
    private readonly log: (line: string) => void,
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
    const command = `${this.executable()} serve --exit-with-stdin`;
    this.log(`starting: ${command}  (in ${this.cwd})`);
    return new Promise<GerClient>((resolve, reject) => {
      const child = spawn(command, { cwd: this.cwd, shell: true, stdio: ["pipe", "pipe", "pipe"] });
      this.child = child;
      let out = "";
      let err = "";
      let settled = false;
      const fail = (reason: string) => {
        if (!settled) {
          settled = true;
          clearTimeout(timer);
          reject(new Error(reason));
        }
      };
      const timer = setTimeout(() => {
        fail(`ger serve did not start within ${STARTUP_TIMEOUT_MS / 1000}s`);
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
        try {
          const hello = JSON.parse(out.slice(0, end)) as Hello;
          settled = true;
          clearTimeout(timer);
          this.log(`ger serve is listening on ${hello.url} for ${hello.repo}`);
          resolve(new GerClient(hello));
        } catch {
          fail(`ger serve printed something unexpected: ${out.slice(0, end)}`);
        }
      });
      child.stderr?.setEncoding("utf8").on("data", (chunk: string) => {
        err += chunk;
        this.log(chunk.trimEnd());
      });
      child.on("error", (error) => fail(`could not run "${command}": ${error.message}`));
      child.on("exit", (code) => {
        this.log(`ger serve exited (${code ?? "killed"})`);
        if (this.child === child) {
          this.child = undefined;
          this.starting = undefined;
        }
        fail(err.trim() || `"${command}" exited with code ${code}`);
      });
    });
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
