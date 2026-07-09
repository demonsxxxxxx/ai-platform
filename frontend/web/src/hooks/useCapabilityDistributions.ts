import { useEffect, useState } from "react";
import { capabilityDistributionApi } from "../services/api/capabilityDistribution";
import type {
  CapabilityDistribution,
  CapabilityDistributionUpdateRequest,
  CapabilityKind,
} from "../types";

function upsertDistributionItem(
  items: CapabilityDistribution[],
  nextItem: CapabilityDistribution,
): CapabilityDistribution[] {
  const nextItems = items.filter(
    (item) => item.capability_id !== nextItem.capability_id,
  );
  nextItems.push(nextItem);
  nextItems.sort((left, right) =>
    left.capability_id.localeCompare(right.capability_id),
  );
  return nextItems;
}

function normalizeErrorMessage(
  error: unknown,
  capabilityId: string | null,
  fallback: string,
): string {
  const baseMessage =
    error instanceof Error && error.message ? error.message : fallback;
  if (!capabilityId) {
    return baseMessage;
  }
  return `${capabilityId}: ${baseMessage}`;
}

export function useCapabilityDistributions(options: {
  capabilityKind: CapabilityKind;
  enabled?: boolean;
}) {
  const capabilityKind = options.capabilityKind;
  const enabled = options.enabled !== false;
  const [items, setItems] = useState<CapabilityDistribution[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pendingCapabilityIds, setPendingCapabilityIds] = useState<string[]>(
    [],
  );

  useEffect(() => {
    if (!enabled) {
      setItems([]);
      setError(null);
      setPendingCapabilityIds([]);
      return;
    }

    let cancelled = false;

    async function load() {
      setIsLoading(true);
      setError(null);
      try {
        const response = await capabilityDistributionApi.list({ capabilityKind });
        if (!cancelled) {
          setItems(response.items ?? []);
        }
      } catch (loadError) {
        if (!cancelled) {
          setError(
            normalizeErrorMessage(
              loadError,
              null,
              "Failed to load capability distributions",
            ),
          );
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false);
        }
      }
    }

    void load();

    return () => {
      cancelled = true;
    };
  }, [capabilityKind, enabled]);

  const getDistribution = (capabilityId: string): CapabilityDistribution | null =>
    items.find((item) => item.capability_id === capabilityId) ?? null;

  const saveDistribution = async (
    capabilityId: string,
    payload: CapabilityDistributionUpdateRequest,
  ): Promise<boolean> => {
    if (!enabled) {
      return false;
    }
    setPendingCapabilityIds((current) =>
      current.includes(capabilityId) ? current : [...current, capabilityId],
    );
    setError(null);
    try {
      const response = await capabilityDistributionApi.update(
        capabilityKind,
        capabilityId,
        payload,
      );
      setItems((current) =>
        upsertDistributionItem(current, response.distribution),
      );
      return true;
    } catch (saveError) {
      setError(
        normalizeErrorMessage(
          saveError,
          capabilityId,
          "Failed to update capability distribution",
        ),
      );
      return false;
    } finally {
      setPendingCapabilityIds((current) =>
        current.filter((item) => item !== capabilityId),
      );
    }
  };

  const toggleDistribution = async (
    capabilityId: string,
    enabledState: boolean,
    fallbackPayload: CapabilityDistributionUpdateRequest,
  ): Promise<boolean> => {
    if (!enabled) {
      return false;
    }
    const current = getDistribution(capabilityId);
    if (current === null) {
      return saveDistribution(capabilityId, {
        ...fallbackPayload,
        status: enabledState ? "active" : "disabled",
      });
    }
    setPendingCapabilityIds((pending) =>
      pending.includes(capabilityId) ? pending : [...pending, capabilityId],
    );
    setError(null);
    try {
      const response = await capabilityDistributionApi.toggle(
        capabilityKind,
        capabilityId,
        enabledState,
      );
      setItems((existing) =>
        upsertDistributionItem(existing, response.distribution),
      );
      return true;
    } catch (toggleError) {
      setError(
        normalizeErrorMessage(
          toggleError,
          capabilityId,
          "Failed to toggle capability distribution",
        ),
      );
      return false;
    } finally {
      setPendingCapabilityIds((pending) =>
        pending.filter((item) => item !== capabilityId),
      );
    }
  };

  return {
    items,
    isLoading,
    error,
    pendingCapabilityIds,
    getDistribution,
    saveDistribution,
    toggleDistribution,
  };
}
