import { getJson } from "./client";
export const listMachines = () => getJson("/products");
export const listRevisions = (productId) => getJson(`/products/${productId}/revisions`);
