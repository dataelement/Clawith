export function isRuntimeReadyModel(model) {
  return model?.enabled === true && model?.supports_tool_calling === true;
}

export function runtimeReadyModels(models) {
  return models.filter(isRuntimeReadyModel);
}

export function resolveEffectiveChatModelId({
  models,
  overrideModelId,
  agentPrimaryModelId,
  tenantDefaultModelId,
}) {
  const readyModels = runtimeReadyModels(models);
  const readyIds = new Set(readyModels.map((model) => model.id));
  const configuredId = [
    overrideModelId,
    agentPrimaryModelId,
    tenantDefaultModelId,
  ].find((modelId) => modelId && readyIds.has(modelId));

  return configuredId || readyModels[0]?.id || null;
}
