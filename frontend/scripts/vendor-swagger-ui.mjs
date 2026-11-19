// Copy the pinned Swagger UI assets into a target directory.
//
// This is a build tool, not a frontend runtime concern: it reads the pinned
// `swagger-ui-dist` package (a frontend devDependency, the source of the exact
// version) and writes the verbatim assets wherever `SWAGGER_UI_TARGET_DIR`
// points.  The integration Makefile points it at the backend's static assets.
//
// Run after changing the swagger-ui-dist version:
//   SWAGGER_UI_TARGET_DIR=../backend/pfa/static/swagger-ui npm run vendor:swagger-ui
import { copyFileSync, mkdirSync, writeFileSync, readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const source = join(here, "..", "node_modules", "swagger-ui-dist");
const targetDir = process.env.SWAGGER_UI_TARGET_DIR;
if (!targetDir) {
  console.error(
    "SWAGGER_UI_TARGET_DIR is required (for example ../backend/pfa/static/swagger-ui).\n" +
      "Frontend and backend are separate projects, so the destination is not assumed.",
  );
  process.exit(1);
}
const target = resolve(targetDir);
const files = [
  "swagger-ui-bundle.js",
  "swagger-ui.css",
  "favicon-32x32.png",
  "favicon-16x16.png",
  "LICENSE",
  "NOTICE",
];
const version = JSON.parse(readFileSync(join(source, "package.json"), "utf8")).version;

mkdirSync(target, { recursive: true });
const lines = [`swagger-ui-dist@${version} (Apache-2.0) - copied verbatim, no modification.`, ""];
for (const name of files) {
  copyFileSync(join(source, name), join(target, name));
  const hash = createHash("sha256").update(readFileSync(join(source, name))).digest("hex");
  lines.push(`  ${hash}  ${name}`);
}
lines.push("", "Not copied: source maps, standalone preset, index.html, oauth2-redirect.html.");
lines.push(
  "Refresh with: SWAGGER_UI_TARGET_DIR=../backend/pfa/static/swagger-ui npm run vendor:swagger-ui",
);
writeFileSync(join(target, "VENDORED.txt"), lines.join("\n") + "\n");
console.log(`vendored swagger-ui-dist@${version} into ${target}`);
