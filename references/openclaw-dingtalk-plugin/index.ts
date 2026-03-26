import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import * as pluginSdk from "openclaw/plugin-sdk";
import { emptyPluginConfigSchema } from "openclaw/plugin-sdk";
import { dingtalkPlugin } from "./src/channel";
import { getConfig } from "./src/config";
import { appendToDoc, createDoc, DocCreateAppendError, listDocs, searchDocs } from "./src/docs-service";
import { setDingTalkRuntime } from "./src/runtime";
import type { DingtalkPluginModule } from "./src/types";

type GatewayMethodContext = Pick<
  Parameters<Parameters<OpenClawPluginApi["registerGatewayMethod"]>[1]>[0],
  "params" | "respond"
>;

const plugin: DingtalkPluginModule = {
  id: "dingtalk",
  name: "DingTalk Channel",
  description: "DingTalk (钉钉) messaging channel via Stream mode",
  configSchema: emptyPluginConfigSchema(),
  register(api: OpenClawPluginApi): void {
    setDingTalkRuntime(api.runtime);
    api.registerChannel({ plugin: dingtalkPlugin });
    api.registerGatewayMethod("dingtalk.docs.create", async ({ respond, params }: GatewayMethodContext) => {
      const accountId = pluginSdk.readStringParam(params, "accountId");
      const spaceId = pluginSdk.readStringParam(params, "spaceId", { required: true });
      const title = pluginSdk.readStringParam(params, "title", { required: true });
      const content = pluginSdk.readStringParam(params, "content", { allowEmpty: true });
      const parentId = pluginSdk.readStringParam(params, "parentId");
      const config = getConfig(api.config, accountId ?? undefined);
      try {
        const doc = await createDoc(
          config,
          spaceId,
          title,
          content ?? undefined,
          api.logger,
          parentId ?? undefined,
        );
        return respond(true, doc);
      } catch (error) {
        if (error instanceof DocCreateAppendError) {
          return respond(true, {
            partialSuccess: true,
            initContentAppended: false,
            docId: error.doc.docId,
            doc: error.doc,
            appendError: error.message,
          });
        }
        throw error;
      }
    });
    api.registerGatewayMethod("dingtalk.docs.append", async ({ respond, params }: GatewayMethodContext) => {
      const accountId = pluginSdk.readStringParam(params, "accountId");
      const docId = pluginSdk.readStringParam(params, "docId", { required: true });
      const content = pluginSdk.readStringParam(params, "content", { required: true, allowEmpty: false });
      const config = getConfig(api.config, accountId ?? undefined);
      const result = await appendToDoc(config, docId, content, api.logger);
      return respond(true, result);
    });
    api.registerGatewayMethod("dingtalk.docs.search", async ({ respond, params }: GatewayMethodContext) => {
      const accountId = pluginSdk.readStringParam(params, "accountId");
      const keyword = pluginSdk.readStringParam(params, "keyword", { required: true });
      const spaceId = pluginSdk.readStringParam(params, "spaceId");
      const config = getConfig(api.config, accountId ?? undefined);
      const docs = await searchDocs(config, keyword, spaceId, api.logger);
      return respond(true, { docs });
    });
    api.registerGatewayMethod("dingtalk.docs.list", async ({ respond, params }: GatewayMethodContext) => {
      const accountId = pluginSdk.readStringParam(params, "accountId");
      const spaceId = pluginSdk.readStringParam(params, "spaceId", { required: true });
      const parentId = pluginSdk.readStringParam(params, "parentId");
      const config = getConfig(api.config, accountId ?? undefined);
      const docs = await listDocs(config, spaceId, parentId, api.logger);
      return respond(true, { docs });
    });
  },
};

export default plugin;
