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
const LETTER = /\p{L}/u;
const STYLING_DIRECTIVES: ReadonlySet<string> = new Set(["class", "style"]);
const IDENTIFIER_CHARACTERS = /[/:#.@*_\-$%<>=[\]{}()?&|\\~+^,;!]/;
const CAMEL_CASE = /^[a-z]+(?:[A-Z][a-z0-9]*)+$/;

// Plain words a file uses as codes (message types, states, colours, option names), by file. A word
// is a code only in the files listed, so "search" in PrInput.vue is still text.
const CODE_WORDS: Readonly<Record<string, readonly string[]>> = {
  "background.ts": [
    "MERGED",
    "CLOSED",
    "settings",
    "normal",
    "object",
    "meta",
    "owners",
    "failures",
    "cursor",
    "states",
    "error",
    "search",
    "panes",
  ],
  "composables/usePanes.ts": ["panes"],
  "composables/usePrPane.ts": [
    "info",
    "secondary",
    "success",
    "warn",
    "found",
    "missing",
    "checking",
    "unchecked",
    "idle",
  ],
  "composables/useRepoPicker.ts": ["repos"],
  "composables/useStatus.ts": ["info", "error"],
  "composables/useSummary.ts": ["summary"],
  "core.ts": [
    "complete",
    "MERGED",
    "CLOSED",
    "OPEN",
    "grey",
    "blue",
    "red",
    "yellow",
    "green",
    "pink",
    "purple",
    "cyan",
    "orange",
  ],
  "i18n.ts": ["one", "other", "conjunction"],
  "messages.ts": ["error", "object", "message", "string", "type", "reply"],
  "popup/components/ActionButton.vue": ["secondary", "run"],
  "popup/components/GoToForm.vue": ["input", "repo", "go", "pick"],
  "popup/components/PrInput.vue": ["field"],
  "popup/components/RepoSelect.vue": ["pick"],
  "popup/primevue.ts": ["none"],
  "texts.ts": ["MERGED"],
};

function isMachineIdentifier(text: string, codes: ReadonlySet<string>): boolean {
  const word = text.trim();

  if (codes.has(word)) {
    return true;
  }

  if (/\s/.test(word) || word.includes("…")) {
    return false;
  }

  return IDENTIFIER_CHARACTERS.test(word) || CAMEL_CASE.test(word);
}

function codesOf(path: string): ReadonlySet<string> {
  const entry = Object.entries(CODE_WORDS).find(
    ([file]) => path.endsWith(`/${file}`) || path === file,
  );

  return new Set(entry?.[1]);
}

// Any letter-bearing string that is not a machine identifier counts as user text.
export function isUserText(text: string, codes: ReadonlySet<string> = new Set()): boolean {
  return LETTER.test(text) && !isMachineIdentifier(text, codes);
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

function scriptProblems(file: ts.SourceFile, codes: ReadonlySet<string>): string[] {
  const found: string[] = [];

  function visit(node: ts.Node): void {
    const text = literalText(node);

    if (text !== null && isUserText(text, codes)) {
      found.push(quoted(text));
    }

    if (!isConsoleCall(node) && !ts.isLiteralTypeNode(node)) {
      ts.forEachChild(node, visit);
    }
  }

  visit(file);

  return found;
}

function typescriptProblems(source: string, codes: ReadonlySet<string> = new Set()): string[] {
  const file = ts.createSourceFile("source.ts", source, ts.ScriptTarget.Latest, true);

  return scriptProblems(file, codes);
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

// Static markup text and visible attributes are user text whenever they hold a letter.
function staticProblems(text: string): string[] {
  return LETTER.test(text) ? [quoted(text)] : [];
}

function expressionProblems(expression: string): string[] {
  return typescriptProblems(expression);
}

function propProblems(prop: TemplateElement["props"][number]): string[] {
  if (isDirective(prop)) {
    return STYLING_DIRECTIVES.has(expressionText(prop.arg))
      ? []
      : expressionProblems(expressionText(prop.exp));
  }

  const isVisible = VISIBLE_ATTRIBUTES.has(prop.name);

  return isVisible ? staticProblems(prop.value?.content ?? "") : [];
}

function ownProblems(node: TemplateNode): string[] {
  if (node.type === TEXT) {
    return staticProblems(expressionText(node));
  }

  if (node.type === INTERPOLATION) {
    return expressionProblems(expressionText("content" in node ? node.content : null));
  }

  return isElement(node) ? node.props.flatMap(propProblems) : [];
}

function nodeProblems(node: TemplateNode): string[] {
  const nested: readonly unknown[] = "children" in node ? node.children : [];

  return [...ownProblems(node), ...nested.filter(isNode).flatMap(nodeProblems)];
}

function vueProblems(source: string, codes: ReadonlySet<string>): string[] {
  const { template, script, scriptSetup } = parse(source).descriptor;
  const fromTemplate = template?.ast ? nodeProblems(template.ast) : [];
  const fromScripts = [script, scriptSetup].flatMap((block) =>
    block ? typescriptProblems(block.content, codes) : [],
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

  return values.filter((value) => LETTER.test(value)).map(quoted);
}

// Every literal user-visible string in a src file, by file type.
export function literalProblems(path: string, source: string): string[] {
  if (path.endsWith(".vue")) {
    return vueProblems(source, codesOf(path));
  }

  return path.endsWith(".html") ? htmlProblems(source) : typescriptProblems(source, codesOf(path));
}
