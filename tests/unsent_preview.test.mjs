import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(new URL("../docs/console/v2-unsent.js", import.meta.url), "utf8");
const window = { RC: {} };
vm.runInNewContext(source, { window });
const toMegaEmbedUrl = window.RC.toMegaEmbedUrl;

test("converts a valid MEGA file URL to its embed URL", () => {
  assert.equal(
    toMegaEmbedUrl("https://mega.nz/file/AbCd1234#ABCDEFGHIJKLMNOPQRSTUV"),
    "https://mega.nz/embed/AbCd1234#ABCDEFGHIJKLMNOPQRSTUV",
  );
});

test("rejects malformed MEGA file IDs and keys", () => {
  assert.equal(toMegaEmbedUrl("https://mega.nz/file/AbCd123#ABCDEFGHIJKLMNOPQRSTUV"), null);
  assert.equal(toMegaEmbedUrl("https://mega.nz/file/AbCd1234#ABCDEFGHIJKLMNOPQRSTU!"), null);
  assert.equal(toMegaEmbedUrl("https://mega.nz/file/AbCd1234#ABCDEFGHIJKLMNOPQRSTUV?x=1"), null);
});

test("rejects non-MEGA hosts and unsupported MEGA domains", () => {
  assert.equal(toMegaEmbedUrl("https://example.com/file/AbCd1234#ABCDEFGHIJKLMNOPQRSTUV"), null);
  assert.equal(toMegaEmbedUrl("https://mega.io/file/AbCd1234#ABCDEFGHIJKLMNOPQRSTUV"), null);
  assert.equal(toMegaEmbedUrl("https://mega.nz.evil.example/file/AbCd1234#ABCDEFGHIJKLMNOPQRSTUV"), null);
});

test("rejects a MEGA file URL with no key", () => {
  assert.equal(toMegaEmbedUrl("https://mega.nz/file/AbCd1234"), null);
  assert.equal(toMegaEmbedUrl("https://mega.nz/file/AbCd1234#"), null);
});

test("rejects non-string input", () => {
  assert.equal(toMegaEmbedUrl(null), null);
  assert.equal(toMegaEmbedUrl({ url: "https://mega.nz/file/AbCd1234#ABCDEFGHIJKLMNOPQRSTUV" }), null);
});
