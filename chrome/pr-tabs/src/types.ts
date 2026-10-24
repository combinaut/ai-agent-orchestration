// Every PR tabs type: domain, chrome messages, native host, storage and popup.
import type { ComputedRef, Ref } from "vue";

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
export type OwnerKey = `${string}:${BranchName}`;
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

export interface PrRef {
  owner: OrgName;
  repo: RepoName;
  number: PrNumber;
}

export interface Pr extends PrRef {
  key: PrKey;
  url: PrUrl;
}

export type TabLike = Partial<Pick<Tab, "url" | "active" | "windowId" | "groupId">> & {
  id: TabId;
};

export interface Entry<T extends TabLike = TabLike> {
  tab: T;
  pr: Pick<Pr, "key">;
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

export interface PrBranch extends Pick<PrRef, "owner" | "repo"> {
  branch?: BranchName;
  headRepository?: HeadRepository | null;
}

export interface GroupPlacement {
  name: GroupTitle;
  color: GroupColor;
}

export type GroupLike = Partial<Pick<TabGroup, "title" | "color">>;

export interface GroupSummary {
  title: GroupTitle;
  color?: GroupColor;
  count: number;
}

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
export type PlainRequestType = "summary" | "repos" | "openAll" | "checkNow" | "closeDuplicates";

export interface PlainRequest<K extends PlainRequestType = PlainRequestType> {
  readonly type: K;
}

export interface PickRepoRequest {
  readonly type: "pickRepo";
  readonly repo: RepoName;
}

export interface GoToRequest {
  readonly type: "goTo";
  readonly input: string;
  readonly repo?: RepoName;
}

export type Request = PlainRequest | PickRepoRequest | GoToRequest;
export type RequestType = Request["type"];
export type RequestOf<K extends RequestType> = K extends PlainRequestType
  ? PlainRequest<K>
  : Extract<Request, { readonly type: K }>;

export interface Failure {
  readonly error: string;
}

export interface Delivered {
  readonly reply: unknown;
}

export interface RepoChoices {
  readonly repos: readonly RepoName[];
  readonly selected: RepoName;
  readonly show: boolean;
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

export interface Replies {
  readonly summary: Summary;
  readonly repos: RepoChoices;
  readonly pickRepo: Readonly<Record<string, never>>;
  readonly goTo: GoToResult;
  readonly openAll: OpenAllResult;
  readonly checkNow: CheckResult;
  readonly closeDuplicates: DuplicatesClosed;
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

export type HostRequest = SettingsRequest | StatesRequest | SearchRequest;

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

export interface HostReplies {
  settings: Partial<Rules>;
  states: StatesReply;
  search: SearchReply;
}

export type HostOk<T> = { ok: true } & T;

export interface HostFailure {
  ok: false;
  error?: string;
}

export type HostReply<T> = HostOk<T> | HostFailure;

export interface StoredState {
  prTabsSettings: HostOk<Partial<Rules>> | null;
  owners: Owners;
  meta: Meta;
  failures: number;
  cursor: number;
  prTabsRepo: RepoName;
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

export interface Status {
  status: Readonly<Ref<StatusLine>>;
  statusText: ComputedRef<string>;
  isStatusError: ComputedRef<boolean>;
  info: (text: string) => void;
  error: (text: string) => void;
}

export interface ServiceWorker {
  ask: Ask;
}

export interface RepoPicker {
  repos: ComputedRef<RepoName[]>;
  repo: Ref<RepoName>;
  available: ComputedRef<boolean>;
  visible: ComputedRef<boolean>;
  load: () => Promise<void>;
  pick: () => void;
}

export interface GoToContext {
  ask: Ask;
  status: Pick<Status, "error">;
  picker: Pick<RepoPicker, "repo"> & { available: Readonly<Ref<boolean>> };
}

export interface GoTo {
  input: Ref<string>;
  busy: Readonly<Ref<boolean>>;
  go: () => Promise<void>;
}

export interface SummaryList {
  groups: Ref<GroupSummary[]>;
  refresh: () => Promise<void>;
}

export type TabActionType = "openAll" | "checkNow" | "closeDuplicates";

export interface TabActionContext {
  ask: Ask;
  status: Pick<Status, "statusText" | "info" | "error">;
  refresh: () => Promise<void>;
}

export interface TabActionSpec<K extends TabActionType> {
  request: PlainRequest<K>;
  pending: string;
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
  dotClass: ComputedRef<string>;
  countLabel: ComputedRef<string>;
}

// Component props.
export interface ActionButtonProps {
  label: string;
  busy: boolean;
  primary?: boolean;
}

export interface GoToFormProps {
  busy: boolean;
  repos: RepoName[];
  showRepo: boolean;
}

export interface PrInputProps {
  readonly: boolean;
}

export interface RepoSelectProps {
  repos: RepoName[];
}

export interface StatusLineProps {
  text: string;
  error: boolean;
}

export interface GroupListProps {
  groups: GroupSummary[];
}

export interface GroupItemProps {
  group: GroupSummary;
}

// Component events.
export interface ActionButtonEmits {
  run: [];
}

export interface GoToFormEmits {
  go: [];
  pick: [];
}

export interface RepoSelectEmits {
  pick: [];
}
