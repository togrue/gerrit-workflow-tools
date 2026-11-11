// The `ger.executable` setting as a program and its arguments, and back to a line a shell runs.
// Pure: no `vscode` import, unit-tested.

/**
 * Split a command line into words: blanks separate them, double or single quotes keep blanks
 * inside one. A backslash is an ordinary character, so Windows paths need no escaping.
 */
export function splitCommandLine(line: string): string[] {
  const words: string[] = [];
  let word: string | undefined;
  let quote: string | undefined;
  for (const char of line) {
    if (quote) {
      if (char === quote) {
        quote = undefined;
      } else {
        word += char;
      }
    } else if (char === '"' || char === "'") {
      quote = char;
      word ??= "";
    } else if (/\s/.test(char)) {
      if (word !== undefined) {
        words.push(word);
        word = undefined;
      }
    } else {
      word = (word ?? "") + char;
    }
  }
  if (word !== undefined) {
    words.push(word);
  }
  return words;
}

/**
 * The program and leading arguments a setting like `ger.executable` names: `ger`, `uv run ger`,
 * `"C:\Program Files\ger\ger.exe"`, or that path unquoted when it is a file. Empty: `fallback`.
 */
export function programArgv(setting: string, fallback: string, isFile: (path: string) => boolean): string[] {
  const line = setting.trim();
  if (line && isFile(line)) {
    return [line];
  }
  const argv = splitCommandLine(line);
  return argv.length ? argv : [fallback];
}

export type ShellKind = "posix" | "powershell" | "cmd";

/** The kind of shell at `shellPath` (VS Code's `env.shell`), for quoting. */
export function shellKind(shellPath: string): ShellKind {
  const name = shellPath.split(/[\\/]/).pop()?.toLowerCase() ?? "";
  if (/^(pwsh|powershell)(\.exe)?$/.test(name)) {
    return "powershell";
  }
  return /^cmd(\.exe)?$/.test(name) ? "cmd" : "posix";
}

const PLAIN = /^[\w@%+=:,./\\-]+$/;

function quote(word: string, shell: ShellKind): string {
  if (PLAIN.test(word)) {
    return word;
  }
  if (shell === "posix") {
    return `'${word.replaceAll("'", `'\\''`)}'`;
  }
  return shell === "powershell" ? `'${word.replaceAll("'", "''")}'` : `"${word}"`;
}

/** `argv` as a line `shell` runs as that program with those arguments. */
export function shellCommandLine(argv: string[], shell: ShellKind): string {
  const line = argv.map((word) => quote(word, shell)).join(" ");
  // PowerShell takes a quoted first word for a string, not a command: call it with `&`.
  return shell === "powershell" && argv.length && !PLAIN.test(argv[0]) ? `& ${line}` : line;
}

/** How to spawn `argv` without a shell; a Windows batch file (`.cmd`, `.bat`) needs cmd.exe. */
export function spawnable(argv: string[], platform: string = process.platform): { command: string; args: string[]; shell: boolean } {
  const [command, ...args] = argv;
  if (platform === "win32" && /\.(cmd|bat)$/i.test(command)) {
    return { command: quote(command, "cmd"), args: args.map((arg) => quote(arg, "cmd")), shell: true };
  }
  return { command, args, shell: false };
}
