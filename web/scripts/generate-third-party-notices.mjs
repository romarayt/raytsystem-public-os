import { createHash } from "node:crypto";
import { existsSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const webRoot = resolve(scriptDirectory, "..");
const lockPath = join(webRoot, "package-lock.json");
const outputPath = join(webRoot, "public", "licenses", "THIRD-PARTY-JS-LICENSES.txt");
const checkOnly = process.argv.includes("--check");

function normalizeText(value) {
  return value.replace(/\r\n?/g, "\n").replace(/[ \t]+$/gm, "").trimEnd();
}

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

const lockBytes = readFileSync(lockPath);
const lock = JSON.parse(lockBytes.toString("utf8"));
const packages = [];
const licenseGroups = new Map();

for (const [lockLocation, metadata] of Object.entries(lock.packages ?? {})) {
  if (!lockLocation.startsWith("node_modules/") || metadata.dev === true) {
    continue;
  }

  const packageDirectory = join(webRoot, lockLocation);
  const manifestPath = join(packageDirectory, "package.json");
  if (!existsSync(manifestPath)) {
    throw new Error(`Resolved production package is not installed: ${lockLocation}`);
  }

  const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
  const name = manifest.name ?? metadata.name;
  const version = manifest.version ?? metadata.version;
  if (!name || !version) {
    throw new Error(`Package identity is incomplete: ${lockLocation}`);
  }

  const evidenceFiles = readdirSync(packageDirectory)
    .filter((entry) => /^(license|licence|copying|notice)(?:[.-]|$)/i.test(entry))
    .sort((left, right) => left.localeCompare(right, "en"));
  if (evidenceFiles.length === 0) {
    throw new Error(`No license or notice evidence found for ${name}@${version}`);
  }

  const packageId = `${name}@${version} (${lockLocation})`;
  packages.push({
    id: packageId,
    declaredLicense: manifest.license ?? metadata.license ?? "not declared",
    evidenceFiles,
  });

  for (const evidenceFile of evidenceFiles) {
    const text = normalizeText(readFileSync(join(packageDirectory, evidenceFile), "utf8"));
    const key = sha256(text);
    const group = licenseGroups.get(key) ?? { text, users: [] };
    group.users.push(`${packageId}: ${evidenceFile}`);
    licenseGroups.set(key, group);
  }
}

packages.sort((left, right) => left.id.localeCompare(right.id, "en"));
const groups = [...licenseGroups.entries()].sort(([left], [right]) => left.localeCompare(right));

const lines = [
  "raytsystem production JavaScript third-party licenses",
  "",
  "This file is generated from the exact production dependency set resolved in",
  "web/package-lock.json. Development-only packages are intentionally excluded.",
  `Lockfile SHA-256: ${sha256(lockBytes)}`,
  `Resolved production package locations: ${packages.length}`,
  "",
  "PACKAGE INDEX",
  "",
];

for (const item of packages) {
  lines.push(`- ${item.id}`);
  lines.push(`  Declared license: ${item.declaredLicense}`);
  lines.push(`  Evidence: ${item.evidenceFiles.join(", ")}`);
}

lines.push("", "LICENSE AND NOTICE TEXTS");

for (const [digest, group] of groups) {
  group.users.sort((left, right) => left.localeCompare(right, "en"));
  lines.push("", `===== ${digest} =====`, "Used by:");
  for (const user of group.users) {
    lines.push(`- ${user}`);
  }
  lines.push("", group.text);
}

const rendered = `${lines.join("\n")}\n`;
if (checkOnly) {
  const current = existsSync(outputPath) ? readFileSync(outputPath, "utf8") : "";
  if (current !== rendered) {
    throw new Error(
      `${relative(webRoot, outputPath)} is stale; run npm --prefix web run licenses:generate`,
    );
  }
} else {
  writeFileSync(outputPath, rendered, "utf8");
}
