import { getJson } from "./client";
export const getSchematicGraph = (documentId) => getJson(`/schematics/${documentId}/graph`);
