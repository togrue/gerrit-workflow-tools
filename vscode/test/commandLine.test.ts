import assert from "node:assert/strict";
import { test } from "node:test";

import { programArgv, shellCommandLine, shellKind, spawnable, splitCommandLine } from "../src/commandLine";

test("a command line splits on blanks, and quotes keep a path with blanks in one word", () => {
  assert.deepEqual(splitCommandLine("ger"), ["ger"]);
  assert.deepEqual(splitCommandLine("  uv run   ger "), ["uv", "run", "ger"]);
  assert.deepEqual(splitCommandLine('"C:\\Program Files\\ger\\ger.exe" --x'), ["C:\\Program Files\\ger\\ger.exe", "--x"]);
  assert.deepEqual(splitCommandLine("uv run --project '/my work/ger' ger"), ["uv", "run", "--project", "/my work/ger", "ger"]);
  assert.deepEqual(splitCommandLine('a "" b'), ["a", "", "b"]);
});

test("ger.executable names a program: a file path as it is, else a command line, else ger", () => {
  const isFile = (path: string) => path === "C:\\Program Files\\ger\\ger.exe";
  assert.deepEqual(programArgv("C:\\Program Files\\ger\\ger.exe", "ger", isFile), ["C:\\Program Files\\ger\\ger.exe"]);
  assert.deepEqual(programArgv("uv run ger", "ger", isFile), ["uv", "run", "ger"]);
  assert.deepEqual(programArgv("   ", "ger", isFile), ["ger"]);
});

test("a command line for the terminal is quoted for its shell", () => {
  const argv = ["C:\\Program Files\\ger.exe", "edit", "it's"];
  assert.equal(shellCommandLine(argv, "powershell"), "& 'C:\\Program Files\\ger.exe' edit 'it''s'");
  assert.equal(shellCommandLine(argv, "cmd"), '"C:\\Program Files\\ger.exe" edit "it\'s"');
  assert.equal(shellCommandLine(["/opt/my ger", "push"], "posix"), "'/opt/my ger' push");
  assert.equal(shellCommandLine(["ger", "push"], "powershell"), "ger push");
  assert.equal(shellKind("C:\\WINDOWS\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"), "powershell");
  assert.equal(shellKind("C:\\Windows\\System32\\cmd.exe"), "cmd");
  assert.equal(shellKind("/bin/zsh"), "posix");
});

test("a program is spawned without a shell, except a Windows batch file", () => {
  assert.deepEqual(spawnable(["uv", "run", "ger"], "win32"), { command: "uv", args: ["run", "ger"], shell: false });
  assert.deepEqual(spawnable(["C:\\my tools\\ger.cmd", "serve"], "win32"), { command: '"C:\\my tools\\ger.cmd"', args: ["serve"], shell: true });
  assert.deepEqual(spawnable(["ger.cmd", "serve"], "linux"), { command: "ger.cmd", args: ["serve"], shell: false });
});
