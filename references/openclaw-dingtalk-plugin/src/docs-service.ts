import axios from "axios";
import { getAccessToken } from "./auth";
import { getLogger } from "./logger-context";
import { getProxyBypassOption } from "./utils";
import type { DingTalkConfig, DocInfo, Logger } from "./types";

const DINGTALK_API = "https://api.dingtalk.com";

async function buildHeaders(config: DingTalkConfig, log?: Logger): Promise<Record<string, string>> {
  const token = await getAccessToken(config, log);
  return {
    "x-acs-dingtalk-access-token": token,
    "Content-Type": "application/json",
  };
}

type CreateDocResponse = {
  docId?: string;
  title?: string;
  name?: string;
  docType?: string;
  creatorId?: string;
  updatedAt?: number | string;
};

type SearchDocItem = {
  docId?: string;
  title?: string;
  docType?: string;
  creatorId?: string;
  updatedAt?: number | string;
};

type ListDentryItem = {
  dentryUuid?: string;
  name?: string;
  dentryType?: string;
  creatorId?: string;
  updatedAt?: number | string;
};

type AppendDocResponse = {
  success?: boolean;
};

export class DocCreateAppendError extends Error {
  readonly doc: DocInfo;

  constructor(doc: DocInfo, cause?: unknown) {
    super("initial content append failed after document creation");
    this.name = "DocCreateAppendError";
    this.doc = doc;
    this.cause = cause;
  }
}

function mapCreatedDoc(item: CreateDocResponse): DocInfo {
  return {
    docId: item.docId ?? "",
    title: item.title ?? item.name ?? "",
    docType: item.docType ?? "unknown",
    creatorId: item.creatorId,
    updatedAt: item.updatedAt,
  };
}

function mapSearchDoc(item: SearchDocItem): DocInfo {
  return {
    docId: item.docId ?? "",
    title: item.title ?? "",
    docType: item.docType ?? "unknown",
    creatorId: item.creatorId,
    updatedAt: item.updatedAt,
  };
}

function mapListDentry(item: ListDentryItem): DocInfo {
  return {
    docId: item.dentryUuid ?? "",
    title: item.name ?? "",
    docType: item.dentryType ?? "unknown",
    creatorId: item.creatorId,
    updatedAt: item.updatedAt,
  };
}

export async function createDoc(
  config: DingTalkConfig,
  spaceId: string,
  title: string,
  content?: string,
  log = getLogger(),
  parentId?: string,
): Promise<DocInfo> {
  const headers = await buildHeaders(config, log);
  const createResp = await axios.post(
    `${DINGTALK_API}/v1.0/doc/spaces/${spaceId}/docs`,
    {
      spaceId,
      ...(parentId ? { parentDentryId: parentId } : { parentDentryId: "" }),
      name: title,
      docType: "alidoc",
    },
    {
      headers,
      timeout: 10_000,
      ...getProxyBypassOption(config),
    },
  );
  const createdBase = mapCreatedDoc((createResp.data ?? {}) as CreateDocResponse);
  const created = {
    ...createdBase,
    title: createdBase.title || title,
    docType: createdBase.docType || "alidoc",
  };
  if (content?.trim() && created.docId) {
    try {
      await appendToDoc(config, created.docId, content, log);
    } catch (error) {
      throw new DocCreateAppendError(created, error);
    }
  }
  return created;
}

export async function appendToDoc(
  config: DingTalkConfig,
  docId: string,
  content: string,
  log = getLogger(),
  index = -1,
): Promise<{ success: true }> {
  const headers = await buildHeaders(config, log);
  // DingTalk document block API accepts `index = -1` to append content at the end.
  const resp = await axios.post(
    `${DINGTALK_API}/v1.0/doc/documents/${docId}/blocks/root/children`,
    {
      blockType: "PARAGRAPH",
      body: { text: content },
      index,
    },
    {
      headers,
      timeout: 10_000,
      ...getProxyBypassOption(config),
    },
  );
  if ((resp.data as AppendDocResponse | undefined)?.success === false) {
    throw new Error("appendToDoc failed");
  }
  return { success: true };
}

export async function searchDocs(
  config: DingTalkConfig,
  keyword: string,
  spaceId?: string,
  log = getLogger(),
): Promise<DocInfo[]> {
  const headers = await buildHeaders(config, log);
  const resp = await axios.post(
    `${DINGTALK_API}/v1.0/doc/docs/search`,
    {
      keyword,
      maxResults: 20,
      ...(spaceId ? { spaceId } : {}),
    },
    {
      headers,
      timeout: 10_000,
      ...getProxyBypassOption(config),
    },
  );
  return Array.isArray(resp.data?.items)
    ? (resp.data.items as SearchDocItem[]).map(mapSearchDoc)
    : [];
}

export async function listDocs(
  config: DingTalkConfig,
  spaceId: string,
  parentId?: string,
  log = getLogger(),
): Promise<DocInfo[]> {
  const headers = await buildHeaders(config, log);
  const resp = await axios.get(`${DINGTALK_API}/v1.0/doc/spaces/${spaceId}/dentries`, {
    headers,
    params: {
      maxResults: 50,
      ...(parentId ? { parentDentryId: parentId } : {}),
    },
    timeout: 10_000,
    ...getProxyBypassOption(config),
  });
  return Array.isArray(resp.data?.items)
    ? (resp.data.items as ListDentryItem[]).map(mapListDentry)
    : [];
}
