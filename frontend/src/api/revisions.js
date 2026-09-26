import { getJson } from "./client";
export const listRevisions = (productId) => getJson(`/products/${productId}/revisions`);
