import { getJson, postJson } from "./client";

export const listMachines = () => getJson("/products");

export async function createMachine(payload) {
  return postJson("/products", payload);
}

export const listRevisions = (productId) =>
  getJson(`/products/${encodeURIComponent(productId)}/revisions`);

export async function createRevision(productId, payload) {
  return postJson(`/products/${encodeURIComponent(productId)}/revisions`, payload);
}
