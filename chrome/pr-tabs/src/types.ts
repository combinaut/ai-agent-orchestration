// Every PR tabs type: domain, chrome messages, native host, storage and popup.
import type { ComputedRef, MaybeRefOrGetter, Ref } from "vue";
import type en from "#locales/en/messages.json";

export type OrgName = string;
export type RepoName = string;
export type BranchName = string;
export type GroupTitle = string;
export type PrNumber = number;
export type TabId = number;
export type WindowId = number;
export type GroupId = number;
export type PrKey = `${OrgName}/${RepoName}#${PrNumber}`;
export type PrUrl = `https://github.com/${OrgName}/${RepoName}/pull/${PrNumber}`;
export type HeadKey = `${string}:${BranchName}`;
export type OwnerKey = HeadKey | PrKey;
export type PrStateName = "OPEN" | "CLOSED" | "MERGED";
export type GroupColor = `${chrome.tabGroups.Color}`;
export type Tab = chrome.tabs.Tab;
export type TabGroup = chrome.tabGroups.TabGroup;
export type IdTab = Tab & { id: TabId };

export interface Rules {
  org: OrgName;
  defaultRepo: RepoName;
  openAll: string;
  repos?: RepoName[];
}

export type RepoRules = Pick<Rules, "defaultRepo" | "repos">;
export type RepoNumbers = Partial<Record<RepoName, PrNumber>>;

export interface PrRef {
  owner: OrgName;
  repo: RepoName;
  number: PrNumber;
}

export interface Pr extends PrRef {
  key: PrKey;
  url: PrUrl;
}

export type UrlTab = Pick<Tab, "url">;
export type TabChange = Pick<chrome.tabs.OnUpdatedInfo, "url" | "status" | "title" | "groupId">;

export type TabLike = Partial<Pick<Tab, "url" | "active" | "windowId" | "groupId">> & {
  id: TabId;
};

export interface Entry<T extends TabLike = TabLike, P extends Pick<Pr, "key"> = Pick<Pr, "key">> {
  tab: T;
  pr: P;
}

export interface PrEntry {
  tab: IdTab;
  pr: Pr;
}

export interface HeadRepository {
  nameWithOwner?: string;
}

export interface PrHead {
  headRefName?: BranchName;
  headRepository?: HeadRepository | null;
}

export interface PrState extends PrHead {
  state?: PrStateName;
  title?: string;
}

export type States = Partial<Record<PrKey, PrState | null>>;
export type Meta = Partial<Record<PrKey, PrState>>;
export type Owners = Partial<Record<OwnerKey, GroupTitle>>;

export interface SearchHit extends PrHead {
  url: string;
  title?: string;
}

export interface SearchResult {
  total: number;
  prs: SearchHit[];
}

export interface Planned {
  hit: SearchHit;
  pr: Pr;
}

export interface Opened {
  hit: Omit<SearchHit, "url">;
  pr: Pick<Pr, "key">;
}

export interface OpenAllPlan {
  open: Planned[];
  remaining: number;
  estimated: boolean;
}

export interface PollBatch {
  batch: PrKey[];
  nextCursor: number;
}

export interface PrBranch extends Pick<PrRef, "owner" | "repo" | "number"> {
  branch?: BranchName;
  headRepository?: HeadRepository | null;
}

export interface GroupPlacement {
  name: GroupTitle;
  color: GroupColor;
}

export type GroupLike = Partial<Pick<TabGroup, "title" | "color">>;

// The repo (owner/name, the head repository for a fork) and branch a PR's head lives on.
export interface BranchRef {
  readonly repo: string;
  readonly branch: BranchName;
}

export interface PrRow {
  readonly tabId: TabId;
  readonly highlights?: PrHighlights;
  readonly key: PrKey;
  readonly label: string;
  readonly title: string;
  readonly head: BranchRef | null;
}

export interface GroupCount {
  id: string;
  title: GroupTitle;
  color?: GroupColor;
  prs: Map<PrKey, PrRow>;
}

export interface GroupSummary {
  id: string;
  title: GroupTitle;
  color?: GroupColor;
  count: number;
  prs: PrRow[];
  // Set while a search filters the group: its PR count before filtering and matched title ranges.
  total?: number;
  titleMatches?: readonly MatchRange[];
}

export type GroupRows = Record<string, GroupSummary>;
export type PrRows = Record<string, PrRow>;

export type AgentState = "working" | "idle" | "done" | "waiting";

export interface PaneMatch {
  readonly state: AgentState | null;
}

// null: no Herdr pane has the branch checked out.
export type PaneLookup = Partial<Record<HeadKey, PaneMatch | null>>;

export interface Summary {
  prs: number;
  groups: GroupSummary[];
}

export interface Closed {
  key: PrKey;
  state?: PrStateName;
}

export interface CheckResult {
  tabs: number;
  prs: number;
  checked: number;
  closed: Closed[];
  inView: PrKey[];
}

export interface OpenAllResult {
  opened: number;
  remaining: number;
  estimated: boolean;
}

// Requests from the popup to the service worker.
export type OpenBatchAction = "openAll" | "openReviews";

export type MetadataRevision = number;

export interface OpeningProgress {
  readonly hydrating?: boolean;
  readonly metadataRevision?: MetadataRevision;
  readonly active: boolean;
  readonly opened: number;
  readonly planned: number;
  readonly result?: Reply<"openAll">;
}

export type PlainRequestType =
  "summary" | "repos" | OpenBatchAction | "openingProgress" | "checkNow" | "closeDuplicates";

export interface PlainRequest<K extends PlainRequestType = PlainRequestType> {
  readonly type: K;
}

export interface PickRepoRequest {
  readonly type: "pickRepo";
  readonly repo: RepoName;
}

export type PrKeyAction = "focusPr" | "closePr";

export interface PrKeyRequest<K extends PrKeyAction = PrKeyAction> {
  readonly type: K;
  readonly key: PrKey;
}

export type FocusPrRequest = PrKeyRequest<"focusPr">;

export interface ClosePrRequest extends PrKeyRequest<"closePr"> {
  readonly tabId: TabId;
}

export interface GoToRequest {
  readonly type: "goTo";
  readonly input: string;
  readonly repo?: RepoName;
}

export interface PanesRequest {
  readonly type: "panes";
  readonly heads: readonly BranchRef[];
}

export interface FocusPaneRequest {
  readonly type: "focusPane";
  readonly head: BranchRef;
}

export type Request =
  | PlainRequest
  | PickRepoRequest
  | GoToRequest
  | FocusPrRequest
  | ClosePrRequest
  | PanesRequest
  | FocusPaneRequest;
export type RequestType = Request["type"];
export type RequestOf<K extends RequestType> = K extends PlainRequestType
  ? PlainRequest<K>
  : Extract<Request, { readonly type: K }>;

export interface Failure {
  readonly error: string;
  readonly tabsOpened?: number;
}

export interface Delivered {
  readonly reply: unknown;
}

export interface RepoChoices {
  readonly repos: readonly RepoName[];
  readonly selected: RepoName;
  readonly show: boolean;
  readonly newest: RepoNumbers;
  readonly guessUnavailable?: boolean;
}

export interface Opening {
  readonly opened: PrKey;
}

export interface Focusing {
  readonly focused: PrKey;
}

export type GoToResult = Opening | Focusing;

export interface DuplicatesClosed {
  readonly closed: number;
}

export interface FocusedPane {
  readonly pane: string | null;
}

export interface Replies {
  readonly summary: Summary;
  readonly repos: RepoChoices;
  readonly pickRepo: Readonly<Record<string, never>>;
  readonly goTo: GoToResult;
  readonly focusPr: Focusing;
  readonly closePr: DuplicatesClosed;
  readonly openAll: OpenAllResult;
  readonly openReviews: OpenAllResult;
  readonly openingProgress: OpeningProgress;
  readonly checkNow: CheckResult;
  readonly closeDuplicates: DuplicatesClosed;
  readonly panes: PaneLookup;
  readonly focusPane: FocusedPane;
}

export type Reply<K extends RequestType> = Replies[K] | Failure;
export type Answer<K extends RequestType> = Reply<K> | void;

export type Handler<K extends RequestType> = (
  message: RequestOf<K>,
) => Answer<K> | Promise<Answer<K>>;

export type Handlers = { readonly [K in RequestType]?: Handler<K> };
export type Send = (message: Request) => Promise<unknown>;
export type Respond = (answer: unknown) => void;
export type UntypedHandler = (request: unknown) => unknown;
export type RuntimeListener = (message: unknown, sender: unknown, respond: Respond) => boolean;
export type Ask = <M extends Request>(message: M) => Promise<Reply<M["type"]>>;

// Requests to the native host and its replies.
export interface SettingsRequest {
  type: "settings";
}

export interface StatesRequest {
  type: "states";
  prs: PrRef[];
}

export interface SearchRequest {
  type: "search";
  query: string;
  limit: number;
  after?: string;
}

export interface PanesHostRequest {
  type: "panes";
  prs: BranchRef[];
}

export interface FocusPaneHostRequest extends BranchRef {
  type: "focusPane";
}

export interface RepoNumbersRequest {
  type: "repoNumbers";
  org: OrgName;
  repos: RepoName[];
}

export type HostRequest =
  | SettingsRequest
  | StatesRequest
  | SearchRequest
  | PanesHostRequest
  | FocusPaneHostRequest
  | RepoNumbersRequest;

export interface Ownership {
  owners?: Owners | null;
}

export interface StatesReply extends Ownership {
  prs: States;
}

export interface SearchReply extends Ownership, SearchResult {
  hasNextPage?: boolean;
  endCursor?: string;
}

export interface PanesReply {
  panes: PaneLookup;
}

export interface RepoNumbersReply {
  newest: RepoNumbers;
}

export interface HostReplies {
  settings: Partial<Rules>;
  states: StatesReply;
  search: SearchReply;
  panes: PanesReply;
  focusPane: FocusedPane;
  repoNumbers: RepoNumbersReply;
}

export type HostOk<T> = { ok: true } & T;

export interface HostFailure {
  ok: false;
  error?: string;
}

export type HostReply<T> = HostOk<T> | HostFailure;

export interface RepoNumbersCache {
  newest: RepoNumbers;
  refreshedAt: number;
}

export interface StoredState {
  prTabsSettings: HostOk<Partial<Rules>> | null;
  owners: Owners;
  meta: Meta;
  failures: number;
  cursor: number;
  prTabsRepo: RepoName;
  prTabsRepoNumbers: RepoNumbersCache;
  prTabsCollapsed: string[];
}

export interface FetchedStates {
  states: States;
  prs: number;
  checked: number;
  nextCursor: number;
}

// The popup's composables.
export type StatusKind = "info" | "error";

export interface StatusLine {
  readonly kind: StatusKind;
  readonly text: string;
}

export type StatusRevision = number;

export interface Status {
  statusText: ComputedRef<string>;
  isStatusError: ComputedRef<boolean>;
  revision: Readonly<Ref<StatusRevision>>;
  info: (text: string, revision?: StatusRevision) => void;
  error: (text: string, revision?: StatusRevision) => void;
}

export interface ServiceWorker {
  ask: Ask;
}

export interface RepoPicker {
  repos: ComputedRef<RepoName[]>;
  repo: Ref<RepoName>;
  available: ComputedRef<boolean>;
  visible: ComputedRef<boolean>;
  guessUnavailable: ComputedRef<boolean>;
  load: () => Promise<void>;
  suggest: () => void;
  pick: () => void;
}

export interface GoToContext {
  ask: Ask;
  status: Pick<Status, "error">;
  picker: Pick<RepoPicker, "repo"> & { available: Readonly<Ref<boolean>> };
  refresh: () => Promise<void>;
}

export type PrNavigationRequest = GoToRequest | FocusPrRequest;

export interface GoTo {
  input: Ref<string>;
  busy: Readonly<Ref<boolean>>;
  go: () => Promise<void>;
  focusPr: (key: PrKey) => Promise<void>;
}

export type PaneAvailability = "unchecked" | "checking" | "found" | "missing";

export type PaneLoader = (heads: readonly BranchRef[]) => Promise<PaneLookup | undefined>;

export interface Panes {
  checking: Readonly<Ref<boolean>>;
  lookup: Readonly<Ref<PaneLookup>>;
  ensure: (heads: readonly BranchRef[]) => Promise<void>;
  focus: (head: BranchRef) => Promise<void>;
}

export interface PrPane {
  availability: ComputedRef<PaneAvailability>;
  isGoVisible: ComputedRef<boolean>;
  isGoDisabled: ComputedRef<boolean>;
  isChecking: ComputedRef<boolean>;
  goLabel: ComputedRef<string>;
  state: ComputedRef<AgentState | null>;
  isStateVisible: ComputedRef<boolean>;
  stateText: ComputedRef<string>;
  stateLabel: ComputedRef<string>;
  stateSeverity: ComputedRef<TagSeverity>;
}

export type TagSeverity = "success" | "info" | "secondary" | "warn";

export interface SummaryList {
  groups: Ref<GroupSummary[]>;
  // The summary line of the last refresh.
  text: Readonly<Ref<string>>;
  refresh: () => Promise<void>;
}

export type TabActionType = OpenBatchAction | "checkNow" | "closeDuplicates";

export interface TabActionContext {
  ask: Ask;
  status: Pick<Status, "statusText" | "isStatusError" | "info" | "error" | "revision">;
  refresh: () => Promise<void>;
}

export type OpeningProgressObserver = (progress: OpeningProgress) => Promise<void>;

export interface OpeningProgressContext extends TabActionContext {
  localBusy: Readonly<Ref<boolean>>;
}

export interface TabActionSpec<K extends TabActionType> {
  request: PlainRequest<K>;
  pending: string;
  refreshOnFailure?: boolean;
  report: (result: Replies[K]) => string;
}

export interface TabAction {
  busy: Readonly<Ref<boolean>>;
  run: () => Promise<void>;
}

export type TabActions = Record<TabActionType, TabAction>;

export interface GroupRow {
  title: ComputedRef<GroupTitle>;
  count: ComputedRef<number>;
  colorClass: ComputedRef<string>;
  countLabel: ComputedRef<string>;
}

// Component props.
export type ProgressMode = "determinate" | "indeterminate";

export interface OpeningProgressProps {
  progress: OpeningProgress;
}

export interface StatusLineProps {
  text: string;
  error: boolean;
}

export interface GroupListProps {
  groups: GroupSummary[];
  panes: Panes;
  collapsed: ReadonlySet<string>;
  selected: string | null;
}

export interface GroupItemProps {
  group: GroupSummary;
  panes: Panes;
  collapsed: ReadonlySet<string>;
  selected: string | null;
}

export interface PrItemProps {
  pr: PrRow;
  panes: Panes;
  selected: string | null;
}

export interface RepoOptionProps {
  option: RepoOption;
  selected: string | null;
}

export interface PopupFieldProps {
  mode: "idle" | "goto" | "search";
  matches: number;
  busy: boolean;
  expanded: boolean;
  active: string | null;
}

export interface ActionMenuItem {
  id: string;
  label: string;
  icon: string;
  busy: boolean;
  separated?: boolean;
  run: () => void;
}

// Component events.
export interface PopupFieldEmits {
  go: [];
  next: [];
  previous: [];
  clear: [];
}

// Chrome i18n: keys and placeholders come from the English catalog.
type MessageCatalog = typeof en;

export type MessageKey = keyof MessageCatalog;

export type PlaceholderName<K extends MessageKey> = MessageCatalog[K] extends {
  placeholders: infer P;
}
  ? keyof P & string
  : never;

export type PlainMessageKey = {
  [K in MessageKey]: [PlaceholderName<K>] extends [never] ? K : never;
}[MessageKey];

export type PlaceholderValue = string | number;

export type NamedValues = Readonly<Record<string, PlaceholderValue>>;

export type MessageArguments<K extends MessageKey> = [PlaceholderName<K>] extends [never]
  ? []
  : [values: Readonly<Record<PlaceholderName<K>, PlaceholderValue>>];

export type PluralBase = {
  [K in MessageKey]: K extends `${infer B}_one`
    ? `${B}_other` extends MessageKey
      ? B
      : never
    : never;
}[MessageKey];

type ExtraPlaceholder<B extends PluralBase> = Exclude<PlaceholderName<`${B}_one`>, "count">;

export type PluralArguments<B extends PluralBase> = [ExtraPlaceholder<B>] extends [never]
  ? []
  : [values: Readonly<Record<ExtraPlaceholder<B>, PlaceholderValue>>];

export type PluralForm = "one" | "other";

export interface CatalogEntry {
  message: string;
  placeholders?: Readonly<Record<string, { content: string }>>;
}

export type Labels<K extends PlainMessageKey> = Readonly<Record<K, string>>;

export interface PrRowEvents {
  go: [key: PrKey];
  close: [key: PrKey, tabId: TabId];
}
export interface RepoOptionEvents {
  pick: [option: RepoOption];
  hover: [option: RepoOption];
}
export interface GroupEvents extends PrRowEvents {
  collapse: [id: string, collapsed: boolean];
}
export type CloseFocus = () => void;
export interface PrCloseContext {
  ask: Ask;
  status: Pick<Status, "error">;
  refresh: () => Promise<void>;
}
export interface PrClose {
  close: (key: PrKey, tabId: TabId) => Promise<void>;
}

export type MatchRange = readonly [number, number];
export interface Segment {
  text: string;
  matched: boolean;
}
export interface PrHighlights {
  number: readonly MatchRange[];
  title: readonly MatchRange[];
}

export interface SearchQuery {
  text: string;
  numberOnly: boolean;
}

export interface SearchFilter {
  groups: ComputedRef<GroupSummary[]>;
  active: ComputedRef<boolean>;
  count: ComputedRef<number>;
}

export interface Collapsed {
  collapsed: Readonly<Ref<ReadonlySet<string>>>;
  load: () => Promise<void>;
  set: (id: string, collapsed: boolean) => void;
  toggle: (id: string) => void;
}

export interface RepoOption {
  readonly id: string;
  readonly repo: RepoName;
  readonly color: GroupColor;
  readonly open: PrKey | null;
}

export interface RepoOptionSources {
  groups: MaybeRefOrGetter<GroupSummary[]>;
  repos: MaybeRefOrGetter<readonly RepoName[]>;
  guessed: MaybeRefOrGetter<RepoName>;
  number: MaybeRefOrGetter<PrNumber | null>;
}

export type Selectable =
  | { readonly kind: "repo"; readonly id: string; readonly option: RepoOption }
  | { readonly kind: "pr"; readonly id: string; readonly row: PrRow };
export interface TextSegmentProps {
  segment: Segment;
}
