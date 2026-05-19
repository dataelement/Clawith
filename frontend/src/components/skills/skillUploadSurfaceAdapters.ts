import type {
  SkillFolderUploadApplyInput,
  SkillFolderUploadApplyResult,
  SkillFolderUploadPreview,
} from '../../services/api';

type SkillUploadSurfaceAdapterDeps = {
  preview: (file: File, targetFolder: string) => Promise<SkillFolderUploadPreview>;
  apply: (input: SkillFolderUploadApplyInput) => Promise<SkillFolderUploadApplyResult>;
  refresh: () => Promise<unknown> | unknown;
};

type SkillUploadSurfaceAdapter = {
  previewRequest: SkillUploadSurfaceAdapterDeps['preview'];
  applyRequest: SkillUploadSurfaceAdapterDeps['apply'];
  onApplied: (result: SkillFolderUploadApplyResult) => Promise<void>;
};

function createSkillUploadSurfaceAdapter({
  preview,
  apply,
  refresh,
}: SkillUploadSurfaceAdapterDeps): SkillUploadSurfaceAdapter {
  return {
    previewRequest: preview,
    applyRequest: apply,
    onApplied: async () => {
      await refresh();
    },
  };
}

export function createEnterpriseSkillUploadSurfaceAdapter(
  deps: SkillUploadSurfaceAdapterDeps,
): SkillUploadSurfaceAdapter {
  return createSkillUploadSurfaceAdapter(deps);
}

export function createAgentSkillUploadSurfaceAdapter(
  deps: SkillUploadSurfaceAdapterDeps,
): SkillUploadSurfaceAdapter {
  return createSkillUploadSurfaceAdapter(deps);
}
