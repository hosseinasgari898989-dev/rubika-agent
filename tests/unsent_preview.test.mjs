import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(new URL("../docs/console/v2-unsent.js", import.meta.url), "utf8");
const window = { RC: {} };
vm.runInNewContext(source, { window });
const toMegaEmbedUrl = window.RC.toMegaEmbedUrl;
const KEY = "A".repeat(43);
const FILE_URL = `https://mega.nz/file/AbCd1234#${KEY}`;

test("converts a valid MEGA file URL to its embed URL", () => {
  assert.equal(
    toMegaEmbedUrl(FILE_URL),
    `https://mega.nz/embed/AbCd1234#${KEY}`,
  );
});

test("rejects malformed MEGA file IDs and keys", () => {
  assert.equal(toMegaEmbedUrl(`https://mega.nz/file/AbCd123#${KEY}`), null);
  assert.equal(toMegaEmbedUrl(`https://mega.nz/file/AbCd1234#${"A".repeat(42)}`), null);
  assert.equal(toMegaEmbedUrl(`https://mega.nz/file/AbCd1234#${"A".repeat(42)}!`), null);
  assert.equal(toMegaEmbedUrl(FILE_URL + "?x=1"), null);
});

test("rejects non-MEGA hosts and unsupported MEGA domains", () => {
  assert.equal(toMegaEmbedUrl(FILE_URL.replace("https://mega.nz", "https://example.com")), null);
  assert.equal(toMegaEmbedUrl(FILE_URL.replace("https://mega.nz", "https://mega.io")), null);
  assert.equal(toMegaEmbedUrl(FILE_URL.replace("https://mega.nz", "https://mega.nz.evil.example")), null);
});

test("rejects a MEGA file URL with no key", () => {
  assert.equal(toMegaEmbedUrl("https://mega.nz/file/AbCd1234"), null);
  assert.equal(toMegaEmbedUrl("https://mega.nz/file/AbCd1234#"), null);
});

test("rejects non-string input", () => {
  assert.equal(toMegaEmbedUrl(null), null);
  assert.equal(toMegaEmbedUrl({ url: FILE_URL }), null);
});
