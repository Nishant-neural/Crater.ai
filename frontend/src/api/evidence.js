export function evidenceSourceLabel(e) {
  return `${e.source_document || "Source document"}${e.page_number ? ` · p.${e.page_number}` : ""}`;
}
