import { getJson, postJson } from "./client";

export const listMachines = () => getJson("/products");

export async function createMachine(payload) {
  return postJson("/products", payload);
}

export const listRevisions = (productId) => getJson(`/products/${productId}/revisions`);

export async function createRevision(productId, payload) {
  return postJson(`/products/${productId}/revisions`, payload);
}
