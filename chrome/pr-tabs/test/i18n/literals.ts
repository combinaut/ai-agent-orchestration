import ts from "typescript";
import { parse } from "vue/compiler-sfc";
import type { TemplateDirective, TemplateElement, TemplateNode } from "#test/types";

const TEXT = 2;
const ELEMENT = 1;
const INTERPOLATION = 5;
const DIRECTIVE = 7;
const VISIBLE_ATTRIBUTES = new Set([
  "label",
  "placeholder",
  "title",
  "alt",
  "header",
  "aria-label",
  "aria-description",
  "aria-placeholder",
  "aria-valuetext",
]);
const WORDS = /\p{L}+[ \u00a0]+[\p{L}\p{N}]/u;
const CAPITALISED_WORD = /^\p{Lu}\p{Ll}{2,}$/u;
const QUOTED = /^(['"`])([\s\S]*)\1$/;

// Prose a user could read: two words, a capitalised word, or an ellipsis.
export function isUserText(text: string): boolean {
  return WORDS.test(text) || CAPITALISED_WORD.test(text.trim()) || text.includes("…");
}

function quoted(text: string): string {
  return `"${text.trim()}"`;
}

function isConsoleCall(node: ts.Node): boolean {
  if (!ts.isCallExpression(node) || !ts.isPropertyAccessExpression(node.expression)) {
    return false;
  }

  const target = node.expression.expression;

  return ts.isIdentifier(target) && target.text === "console";
}

function isModuleSpecifier(node: ts.Node): boolean {
  const parent = node.parent;

  return ts.isImportDeclaration(parent) || ts.isExportDeclaration(parent);
}

function literalText(node: ts.Node): string | null {
  if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) {
    return isModuleSpecifier(node) ? null : node.text;
  }

  if (ts.isTemplateHead(node) || ts.isTemplateMiddle(node) || ts.isTemplateTail(node)) {
    return node.text;
  }

  return null;
}

function scriptProblems(file: ts.SourceFile): string[] {
  const found: string[] = [];

  function visit(node: ts.Node): void {
    const text = literalText(node);

    if (text !== null && isUserText(text)) {
      found.push(quoted(text));
    }

    if (!isConsoleCall(node) && !ts.isLiteralTypeNode(node)) {
      ts.forEachChild(node, visit);
    }
  }

  visit(file);

  return found;
}

function typescriptProblems(source: string): string[] {
  return scriptProblems(ts.createSourceFile("source.ts", source, ts.ScriptTarget.Latest, true));
}

function isNode(value: unknown): value is TemplateNode {
  return typeof value === "object" && value !== null && "type" in value;
}

function isElement(node: TemplateNode): node is TemplateElement {
  return node.type === ELEMENT;
}

function isDirective(prop: TemplateElement["props"][number]): prop is TemplateDirective {
  return prop.type === DIRECTIVE;
}

function expressionText(value: unknown): string {
  const isSimple = typeof value === "object" && value !== null && "content" in value;

  return isSimple && typeof value.content === "string" ? value.content : "";
}

function textProblems(text: string): string[] {
  return isUserText(text) ? [quoted(text)] : [];
}

function quotedProblems(expression: string): string[] {
  const inner = QUOTED.exec(expression.trim())?.[2];

  return inner === undefined ? [] : textProblems(inner);
}

function propProblems(prop: TemplateElement["props"][number]): string[] {
  if (isDirective(prop)) {
    return quotedProblems(expressionText(prop.exp));
  }

  const isVisible = VISIBLE_ATTRIBUTES.has(prop.name);

  return isVisible ? textProblems(prop.value?.content ?? "") : [];
}

function ownProblems(node: TemplateNode): string[] {
  if (node.type === TEXT) {
    return textProblems(expressionText(node));
  }

  if (node.type === INTERPOLATION) {
    return quotedProblems(expressionText("content" in node ? node.content : null));
  }

  return isElement(node) ? node.props.flatMap(propProblems) : [];
}

function nodeProblems(node: TemplateNode): string[] {
  const nested: readonly unknown[] = "children" in node ? node.children : [];

  return [...ownProblems(node), ...nested.filter(isNode).flatMap(nodeProblems)];
}

function vueProblems(source: string): string[] {
  const { template, script, scriptSetup } = parse(source).descriptor;
  const fromTemplate = template?.ast ? nodeProblems(template.ast) : [];
  const fromScripts = [script, scriptSetup].flatMap((block) =>
    block ? typescriptProblems(block.content) : [],
  );

  return [...fromTemplate, ...fromScripts];
}

function htmlProblems(source: string): string[] {
  const markup = source
    .replaceAll(/<!--[\s\S]*?-->/g, "")
    .replaceAll(/<(script|style)[\s\S]*?<\/\1>/g, "");
  const texts = markup.replaceAll(/<[^>]*>/g, "\n").split("\n");
  const attributes = [...markup.matchAll(/\s(?:title|alt|placeholder|aria-label)="([^"]*)"/g)];
  const values = [...texts, ...attributes.map((match) => match[1] ?? "")];

  return values.filter(isUserText).map(quoted);
}

// Every literal user-visible string in a src file, by file type.
export function literalProblems(path: string, source: string): string[] {
  if (path.endsWith(".vue")) {
    return vueProblems(source);
  }

  return path.endsWith(".html") ? htmlProblems(source) : typescriptProblems(source);
}
