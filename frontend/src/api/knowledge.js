import { getJson } from "./client";
export const getMachineKnowledge = (revisionId) => getJson(`/knowledge/revisions/${revisionId}`);
export const getMachineModel = (revisionId) => getJson(`/knowledge/revisions/${revisionId}/model`);
