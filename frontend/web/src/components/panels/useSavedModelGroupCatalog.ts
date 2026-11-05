import { useCallback, useEffect, useRef, useState } from "react";
import { modelAdminApi, type AdminModelEntry, type AdminModelProvider } from "../../services/api/modelAdmin";
import type { AdminModelGroupSummary } from "../../services/api/modelGroups";

type SavedGroupCatalog = {
  groupId: string;
  providerId?: string;
} & (
  | { status: "loading" }
  | { status: "error"; error: string }
  | { status: "ready"; models: AdminModelEntry[]; providers: AdminModelProvider[] }
);
interface SavedGroupCatalogRequestOwner {
  groupId: string;
  providerId?: string;
  sequence: number;
  controller: AbortController;
}

export function useSavedModelGroupCatalog(
  providerId: string | undefined,
  editingGroup: AdminModelGroupSummary | null,
  formatError: (error: unknown) => string,
) {
  const [savedGroupCatalog, setSavedGroupCatalog] = useState<SavedGroupCatalog | null>(null);
  const savedGroupCatalogRequestRef = useRef<SavedGroupCatalogRequestOwner | null>(null);
  const savedGroupCatalogRequestSequence = useRef(0);

  const loadSavedGroupCatalog = useCallback((group: AdminModelGroupSummary, targetProviderId = providerId) => {
    savedGroupCatalogRequestRef.current?.controller.abort();
    const owner: SavedGroupCatalogRequestOwner = {
      groupId: group.id,
      providerId: targetProviderId,
      sequence: ++savedGroupCatalogRequestSequence.current,
      controller: new AbortController(),
    };
    savedGroupCatalogRequestRef.current = owner;
    setSavedGroupCatalog({ groupId: owner.groupId, providerId: owner.providerId, status: "loading" });

    // Group editing always uses the persisted, cross-supplier catalog. The provider-scoped
    // editor state may contain unpublished drafts and must never feed this projection.
    void Promise.resolve().then(() => modelAdminApi.get({ signal: owner.controller.signal })).then((catalog) => {
      if (savedGroupCatalogRequestRef.current !== owner
        || owner.controller.signal.aborted
        || owner.sequence !== savedGroupCatalogRequestSequence.current) return;
      setSavedGroupCatalog({
        groupId: owner.groupId,
        providerId: owner.providerId,
        status: "ready",
        models: catalog.models,
        providers: catalog.providers ?? [],
      });
    }).catch((caught) => {
      if (savedGroupCatalogRequestRef.current !== owner
        || owner.controller.signal.aborted
        || owner.sequence !== savedGroupCatalogRequestSequence.current) return;
      setSavedGroupCatalog({
        groupId: owner.groupId,
        providerId: owner.providerId,
        status: "error",
        error: formatError(caught),
      });
    });
  }, [providerId, formatError]);

  const clearSavedGroupCatalog = useCallback(() => {
    savedGroupCatalogRequestRef.current?.controller.abort();
    savedGroupCatalogRequestRef.current = null;
    savedGroupCatalogRequestSequence.current += 1;
    setSavedGroupCatalog(null);
  }, []);

  const previousGroupCatalogProviderId = useRef(providerId);
  useEffect(() => {
    if (previousGroupCatalogProviderId.current === providerId) return;
    previousGroupCatalogProviderId.current = providerId;
    if (editingGroup) loadSavedGroupCatalog(editingGroup, providerId);
  }, [providerId, editingGroup, loadSavedGroupCatalog]);

  useEffect(() => () => {
    savedGroupCatalogRequestRef.current?.controller.abort();
    savedGroupCatalogRequestRef.current = null;
    savedGroupCatalogRequestSequence.current += 1;
  }, []);

  return { savedGroupCatalog, loadSavedGroupCatalog, clearSavedGroupCatalog };
}
