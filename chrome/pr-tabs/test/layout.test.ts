import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";
import { test } from "vitest";

const ROOT = fileURLToPath(new URL("..", import.meta.url));
const SCRIPT = /<script[^>]*>([\s\S]*?)<\/script>/;

function sourceFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);

    if (entry.isDirectory()) {
      return sourceFiles(path);
    }

    return /\.(?:ts|vue)$/.test(path) ? [path] : [];
  });
}

function scriptOf(path: string): string {
  const text = readFileSync(path, "utf8");

  return path.endsWith(".vue") ? (SCRIPT.exec(text)?.[1] ?? "") : text;
}

function statementLists(node: ts.Node): (readonly ts.Statement[])[] {
  const hasStatements =
    ts.isBlock(node) || ts.isSourceFile(node) || ts.isCaseClause(node) || ts.isDefaultClause(node);
  const own = hasStatements ? [node.statements] : [];
  const nested = node.getChildren().flatMap(statementLists);

  return [...own, ...nested];
}

function crowdedIn(file: ts.SourceFile, statements: readonly ts.Statement[]): ts.Statement[] {
  return statements.filter((statement, index) => {
    const previous = statements[index - 1];
    const gap = previous ? file.text.slice(previous.end, statement.getStart(file)) : "\n\n";

    return ts.isReturnStatement(statement) && !/\n[ \t]*\n/.test(gap);
  });
}

// Every return that directly follows another statement of its block, as "line: return …".
function crowdedReturns(name: string, text: string): string[] {
  const file = ts.createSourceFile(name, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);

  return statementLists(file)
    .flatMap((statements) => crowdedIn(file, statements))
    .map((statement) => {
      const { line } = file.getLineAndCharacterOfPosition(statement.getStart(file));

      return `${line + 1}: ${statement.getText(file).split("\n")[0]}`;
    });
}

test("every closing return has a blank line before it", () => {
  const files = [...sourceFiles(join(ROOT, "src")), ...sourceFiles(join(ROOT, "test"))];
  const found = files.flatMap((path) =>
    crowdedReturns(path, scriptOf(path)).map((hit) => `${relative(ROOT, path)}:${hit}`),
  );

  assert.deepEqual(found, []);
});

test("the check finds a return right after a statement", () => {
  const sample = "function f() {\n  const a = 1;\n  return a;\n}\nfunction g() {\n  return 2;\n}\n";

  assert.deepEqual(crowdedReturns("sample.ts", sample), ["3: return a;"]);
});
