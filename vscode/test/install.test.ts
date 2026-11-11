import assert from "node:assert/strict";
import { test } from "node:test";

import { installArgv, installedGer, onPath, releaseUrl } from "../src/install";

const files =
  (...paths: string[]) =>
  (candidate: string) =>
    paths.includes(candidate);

test("the release asked for is the one with the extension's version", () => {
  const url = "https://github.com/togrue/gerrit-workflow-tools/releases/download/v1.2.3/ger.tar.gz";
  assert.equal(releaseUrl("https://github.com/togrue/gerrit-workflow-tools", "1.2.3"), url);
  assert.equal(releaseUrl("https://github.com/togrue/gerrit-workflow-tools.git/", "1.2.3"), url);
});

test("a program is found on the PATH the way the platform finds it", () => {
  assert.equal(onPath("uv", { PATH: "/usr/bin:/home/me/.local/bin" }, "linux", files("/home/me/.local/bin/uv")), "/home/me/.local/bin/uv");
  assert.equal(onPath("uv", { PATH: "/usr/bin" }, "linux", files("/home/me/.local/bin/uv")), undefined);
  assert.equal(onPath("uv", { Path: "C:\\Windows;C:\\tools", PATHEXT: ".EXE;.CMD" }, "win32", files("C:\\tools\\uv.CMD")), "C:\\tools\\uv.CMD");
  assert.equal(onPath("uv", {}, "linux", files("uv")), undefined);
});

test("ger is installed with uv, else pipx, else not from here", () => {
  assert.deepEqual(
    installArgv("URL", () => true),
    ["uv", "tool", "install", "--force", "URL"],
  );
  assert.deepEqual(
    installArgv("URL", (program) => program === "pipx"),
    ["pipx", "install", "--force", "URL"],
  );
  assert.equal(
    installArgv("URL", () => false),
    undefined,
  );
});

test("a fresh install is found where uv and pipx put it", () => {
  assert.equal(installedGer("/home/me", "linux", files("/home/me/.local/bin/ger")), "/home/me/.local/bin/ger");
  assert.equal(installedGer("C:\\Users\\me", "win32", files("C:\\Users\\me\\.local\\bin\\ger.exe")), "C:\\Users\\me\\.local\\bin\\ger.exe");
  assert.equal(installedGer("/home/me", "linux", files()), undefined);
});
