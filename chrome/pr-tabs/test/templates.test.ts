import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "vitest";
import { parse } from "vue/compiler-sfc";

const SRC = fileURLToPath(new URL("../src", import.meta.url));
const ELEMENT = 1;
const INTERPOLATION = 5;
const DIRECTIVE = 7;
const VARIABLE = /^[A-Za-z_$][\w$]*$/;
const HANDLER = /^handle[A-Z][\w$]*$/;
const V_FOR = /^\s*(?:\([^)]*\)|[\w$]+)\s+(?:in|of)\s+(.+?)\s*$/;

interface Directive {
  type: number;
  name: string;
  rawName?: string;
  exp?: unknown;
}
interface TemplateNode {
  type: number;
  content?: unknown;
  props?: readonly Directive[];
  children?: readonly unknown[];
}

function isNode(value: unknown): value is TemplateNode {
  return typeof value === "object" && value !== null && "type" in value;
}

// The source text of a simple expression, else null.
function expressionText(value: unknown): string | null {
  const isSimple = typeof value === "object" && value !== null && "content" in value;
  return isSimple && typeof value.content === "string" ? value.content : null;
}

function vueFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) {
      return vueFiles(path);
    }
    return path.endsWith(".vue") ? [path] : [];
  });
}

// What a directive or interpolation binds; for v-for, the list it iterates.
function bound(name: string, content: string): string {
  if (name !== "for") {
    return content.trim();
  }
  return V_FOR.exec(content)?.[1] ?? content;
}

function directiveProblems(prop: Directive): string[] {
  const text = expressionText(prop.exp);
  if (text === null) {
    return [];
  }
  const rule = prop.name === "on" ? HANDLER : VARIABLE;
  return rule.test(bound(prop.name, text)) ? [] : [`${prop.rawName ?? prop.name}="${text}"`];
}

function interpolationProblems(node: TemplateNode): string[] {
  const text = node.type === INTERPOLATION ? expressionText(node.content) : null;
  return text === null || VARIABLE.test(text.trim()) ? [] : [`{{${text}}}`];
}

// Every handler that is not a handle<Thing><Event> name and every binding that is more than a variable.
function problems(node: TemplateNode): string[] {
  const props = node.type === ELEMENT ? (node.props ?? []) : [];
  const directives = props.filter((prop) => prop.type === DIRECTIVE).flatMap(directiveProblems);
  const children = (node.children ?? []).filter(isNode).flatMap(problems);
  return [...interpolationProblems(node), ...directives, ...children];
}

function templateProblems(source: string): string[] {
  const ast = parse(source).descriptor.template?.ast;
  return ast ? problems(ast) : [];
}

test("templates bind variables only and name every handler handle<Thing><Event>", () => {
  const found = vueFiles(SRC).flatMap((path) =>
    templateProblems(readFileSync(path, "utf8")).map(
      (problem) => `${relative(SRC, path)}: ${problem}`,
    ),
  );
  assert.deepEqual(found, []);
});

test("the check catches inline handlers and logic in bindings", () => {
  const sample = `<template>
  <div v-if="!busy" :class="a ? 'x' : 'y'" @click="count++" @submit="submit">
    {{ items.length }}
    <li v-for="item in items.filter(Boolean)" v-show="shown" @input="handleFieldInput" />
  </div>
</template>`;
  assert.deepEqual(templateProblems(sample), [
    'v-if="!busy"',
    `:class="a ? 'x' : 'y'"`,
    '@click="count++"',
    '@submit="submit"',
    "{{items.length}}",
    'v-for="item in items.filter(Boolean)"',
  ]);
});
