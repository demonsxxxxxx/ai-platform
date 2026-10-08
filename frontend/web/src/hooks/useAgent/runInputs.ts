import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  sessionApi,
  type RunInputAnswer,
  type RunInputRecord,
  type RunInputSubmissionRequest,
} from "../../services/api/session";
import { ApiRequestError } from "../../services/api/fetch";
import { uuid } from "../../utils/uuid";
import { useRunInputHistory } from "./runInputHistory";
import type {
  RunInputAcceptedSubmission,
  RunInputPendingSubmission,
  RunInputsController,
} from "./types";

interface RunInputAttempt {
  owner: string;
  request: RunInputSubmissionRequest;
}

interface RunInputsState {
  owner: string | null;
  projection: RunInputsController["projection"];
  isLoading: boolean;
  loadFailed: boolean;
  isClosed: boolean;
  submissionError: string | null;
  pendingSubmission: RunInputPendingSubmission | null;
  lastAcceptedSubmission: RunInputAcceptedSubmission | null;
}

function ownerKey(sessionId: string | null, runId: string | null, identityKey: string): string | null {
  return sessionId && runId ? JSON.stringify([sessionId, runId, identityKey]) : null;
}

function viewOfAttempt(
  request: RunInputSubmissionRequest,
  state: RunInputPendingSubmission["state"],
): RunInputPendingSubmission {
  return "text" in request
    ? { inputId: request.input_id, kind: "text", state, text: request.text }
    : {
        inputId: request.input_id,
        kind: "answer",
        state,
        questionId: request.question_id,
      };
}

function acceptedView(request: RunInputSubmissionRequest): RunInputAcceptedSubmission {
  return "text" in request
    ? { inputId: request.input_id, kind: "text", text: request.text }
    : {
        inputId: request.input_id,
        kind: "answer",
        questionId: request.question_id,
      };
}

function makeOptimisticInput(
  request: RunInputSubmissionRequest,
  status: RunInputRecord["status"],
): RunInputRecord {
  return "text" in request
    ? {
        input_id: request.input_id,
        kind: "text",
        text: request.text,
        question_id: null,
        answers: null,
        status,
        created_at: new Date().toISOString(),
      }
    : {
        input_id: request.input_id,
        kind: "answer",
        text: null,
        question_id: request.question_id,
        answers: request.answers,
        status,
        created_at: new Date().toISOString(),
      };
}

function errorKind(error: unknown): string {
  if (error instanceof ApiRequestError && error.status === 409 && error.code === "run_input_closed") {
    return "closed";
  }
  if (
    error instanceof ApiRequestError &&
    error.status >= 400 && error.status < 500 &&
    error.status !== 408 && error.status !== 429
  ) {
    return "rejected";
  }
  return "network";
}

export function useRunInputs({
  sessionId,
  runId,
  isRunActive,
  identityKey = "",
}: {
  sessionId: string | null;
  runId: string | null;
  isRunActive: boolean;
  identityKey?: string;
}): RunInputsController {
  const owner = ownerKey(sessionId, runId, identityKey);
  const ownerRef = useRef(owner);
  const ownerGenerationRef = useRef(0);
  const readSequenceRef = useRef(0);
  const readControllerRef = useRef<AbortController | null>(null);
  const writeControllerRef = useRef<AbortController | null>(null);
  const pendingAttemptRef = useRef<RunInputAttempt | null>(null);
  const [state, setState] = useState<RunInputsState>({
    owner,
    projection: null,
    isLoading: owner !== null,
    loadFailed: false,
    isClosed: false,
    submissionError: null,
    pendingSubmission: null,
    lastAcceptedSubmission: null,
  });
  const history = useRunInputHistory({
    sessionId, identityKey, runId, isRunActive,
    projection: state.owner === owner ? state.projection : null,
  });
  const retireHistory = history.retire;

  useLayoutEffect(() => {
    if (ownerRef.current === owner) return;
    ownerRef.current = owner;
    ownerGenerationRef.current += 1;
    readSequenceRef.current += 1;
    readControllerRef.current?.abort();
    readControllerRef.current = null;
    writeControllerRef.current?.abort();
    writeControllerRef.current = null;
    pendingAttemptRef.current = null;
    setState({
      owner,
      projection: null,
      isLoading: owner !== null,
      loadFailed: false,
      isClosed: false,
      submissionError: null,
      pendingSubmission: null,
      lastAcceptedSubmission: null,
    });
  }, [owner]);

  useEffect(
    () => () => {
      ownerGenerationRef.current += 1;
      readSequenceRef.current += 1;
      readControllerRef.current?.abort();
      writeControllerRef.current?.abort();
      pendingAttemptRef.current = null;
    },
    [],
  );

  const retire = useCallback(() => {
    retireHistory();
    ownerRef.current = null;
    ownerGenerationRef.current += 1;
    readSequenceRef.current += 1;
    readControllerRef.current?.abort();
    writeControllerRef.current?.abort();
    pendingAttemptRef.current = null;
    setState((previous) => ({
      ...previous, projection: null, isLoading: false, loadFailed: false,
      isClosed: true, pendingSubmission: null, lastAcceptedSubmission: null,
      submissionError: null,
    }));
  }, [retireHistory]);

  const readSnapshot = useCallback(async () => {
    if (!sessionId || !runId || !owner || ownerRef.current !== owner) return false;
    const generation = ownerGenerationRef.current;
    const sequence = ++readSequenceRef.current;
    readControllerRef.current?.abort();
    const controller = new AbortController();
    readControllerRef.current = controller;
    const isCurrent = () =>
      ownerRef.current === owner &&
      ownerGenerationRef.current === generation &&
      readSequenceRef.current === sequence &&
      !controller.signal.aborted;

    setState((previous) =>
      previous.owner === owner
        ? { ...previous, isLoading: previous.projection === null }
        : previous,
    );
    try {
      const projection = await sessionApi.getRunInputs(runId, {
        signal: controller.signal,
      });
      if (!isCurrent()) return false;
      let accepted: RunInputAcceptedSubmission | null = null;
      const pending = pendingAttemptRef.current;
      if (
        pending?.owner === owner &&
        projection.inputs.some((input) => input.input_id === pending.request.input_id)
      ) {
        pendingAttemptRef.current = null;
        accepted = acceptedView(pending.request);
      }
      setState((previous) =>
        previous.owner === owner
          ? {
              ...previous,
              projection,
              isLoading: false,
              loadFailed: false,
              isClosed: projection.state !== "open",
              submissionError: accepted ? null : previous.submissionError,
              pendingSubmission: accepted ? null : previous.pendingSubmission,
              lastAcceptedSubmission: accepted ?? previous.lastAcceptedSubmission,
            }
          : previous,
      );
      return true;
    } catch {
      if (!isCurrent()) return false;
      setState((previous) =>
        previous.owner === owner
          ? { ...previous, isLoading: false, loadFailed: true }
          : previous,
      );
      return false;
    } finally {
      if (readControllerRef.current === controller) {
        readControllerRef.current = null;
      }
    }
  }, [owner, runId, sessionId]);

  useEffect(() => {
    if (!owner) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let finalAttempts = 0;
    const poll = async () => {
      const readSucceeded = await readSnapshot();
      finalAttempts += 1;
      if (active && (isRunActive || (!readSucceeded && finalAttempts < 3))) {
        timer = setTimeout(() => void poll(), 1_000);
      }
    };
    void poll();
    return () => {
      active = false;
      if (timer) clearTimeout(timer);
      readControllerRef.current?.abort();
    };
  }, [isRunActive, owner, readSnapshot]);

  const executeAttempt = useCallback(
    async (attempt: RunInputAttempt): Promise<boolean> => {
      if (
        !runId ||
        attempt.owner !== owner ||
        ownerRef.current !== owner ||
        pendingAttemptRef.current !== attempt
      ) {
        return false;
      }
      const generation = ownerGenerationRef.current;
      writeControllerRef.current?.abort();
      const controller = new AbortController();
      writeControllerRef.current = controller;
      const isCurrent = () =>
        ownerRef.current === owner &&
        ownerGenerationRef.current === generation &&
        pendingAttemptRef.current === attempt &&
        !controller.signal.aborted;
      setState((previous) =>
        previous.owner === owner
          ? {
              ...previous,
              submissionError: null,
              lastAcceptedSubmission: null,
              pendingSubmission: viewOfAttempt(attempt.request, "submitting"),
            }
          : previous,
      );

      try {
        const result = await sessionApi.submitRunInput(
          runId,
          attempt.request,
          { signal: controller.signal },
        );
        if (!isCurrent()) return false;
        const acceptedInput = makeOptimisticInput(attempt.request, result.status);
        pendingAttemptRef.current = null;
        setState((previous) => {
          if (previous.owner !== owner) return previous;
          const base = previous.projection ?? {
            run_id: runId,
            state: "open" as const,
            inputs: [],
            questions: [],
          };
          const inputs = base.inputs.some((input) => input.input_id === acceptedInput.input_id)
            ? base.inputs.map((input) =>
                input.input_id === acceptedInput.input_id ? acceptedInput : input,
              )
            : [...base.inputs, acceptedInput];
          return {
            ...previous,
            projection: { ...base, inputs },
            isLoading: false,
            isClosed: false,
            submissionError: null,
            pendingSubmission: null,
            lastAcceptedSubmission: acceptedView(attempt.request),
          };
        });
        void readSnapshot();
        return true;
      } catch (error) {
        if (!isCurrent()) return false;
        const kind = errorKind(error);
        if (kind === "closed") {
          pendingAttemptRef.current = null;
          setState((previous) =>
            previous.owner === owner
              ? {
                  ...previous,
                  isClosed: true,
                  submissionError: "closed",
                  pendingSubmission: null,
                }
              : previous,
          );
          return false;
        }
        if (kind === "rejected") {
          pendingAttemptRef.current = null;
          setState((previous) =>
            previous.owner === owner
              ? {
                  ...previous,
                  submissionError: "rejected",
                  pendingSubmission: null,
                }
              : previous,
          );
          return false;
        }
        setState((previous) =>
          previous.owner === owner
            ? {
                ...previous,
                submissionError: "network",
                pendingSubmission: viewOfAttempt(attempt.request, "uncertain"),
              }
            : previous,
        );
        void readSnapshot();
        return false;
      } finally {
        if (writeControllerRef.current === controller) {
          writeControllerRef.current = null;
        }
      }
    },
    [owner, readSnapshot, runId],
  );

  const submitRequest = useCallback(
    (buildRequest: (inputId: string) => RunInputSubmissionRequest): Promise<boolean> => {
      if (
        !owner ||
        !isRunActive ||
        ownerRef.current !== owner ||
        !state.projection ||
        state.owner !== owner ||
        state.isClosed ||
        state.projection.state !== "open" ||
        pendingAttemptRef.current !== null
      ) {
        return Promise.resolve(false);
      }
      let attempt: RunInputAttempt;
      try {
        attempt = { owner, request: buildRequest(uuid()) };
      } catch {
        setState((previous) =>
          previous.owner === owner
            ? { ...previous, submissionError: "network" }
            : previous,
        );
        return Promise.resolve(false);
      }
      pendingAttemptRef.current = attempt;
      return executeAttempt(attempt);
    },
    [executeAttempt, isRunActive, owner, state.isClosed, state.owner, state.projection],
  );

  const submitText = useCallback(
    (text: string) => {
      const trimmed = text.trim();
      if (!trimmed) {
        setState((previous) =>
          previous.owner === owner
            ? { ...previous, submissionError: "empty" }
            : previous,
        );
        return Promise.resolve(false);
      }
      if (trimmed.length > 16_000) {
        setState((previous) =>
          previous.owner === owner
            ? { ...previous, submissionError: "too_long" }
            : previous,
        );
        return Promise.resolve(false);
      }
      return submitRequest((input_id) => ({ input_id, text: trimmed }));
    },
    [owner, submitRequest],
  );

  const submitAnswers = useCallback(
    (questionId: string, answers: Record<string, RunInputAnswer>) => {
      const batch = state.projection?.questions.find(
        (question) => question.question_id === questionId && question.status === "pending",
      );
      const questionNames = batch?.questions.map((question) => question.key) ?? [];
      const answerNames = Object.keys(answers);
      const complete = Boolean(
        batch &&
          answerNames.length === questionNames.length &&
          questionNames.every((name) => {
            if (!Object.hasOwn(answers, name)) return false;
            const answer = answers[name];
            const question = batch.questions.find((item) => item.key === name);
            if (typeof answer === "string") return Boolean(!question?.multiSelect && question?.options.some((option) => option.key === answer));
            if (!Array.isArray(answer)) return Boolean(answer && typeof answer.text === "string" && answer.text.trim().length > 0 && answer.text.length <= 16_000);
            return Boolean(
              question?.multiSelect &&
                answer.length > 0 &&
                new Set(answer).size === answer.length &&
                answer.every((key) => question.options.some((option) => option.key === key)),
            );
          }),
      );
      if (!complete) {
        setState((previous) =>
          previous.owner === owner
            ? { ...previous, submissionError: "answer_required" }
            : previous,
        );
        return Promise.resolve(false);
      }
      return submitRequest((input_id) => ({ input_id, question_id: questionId, answers }));
    },
    [owner, state.projection, submitRequest],
  );

  const retryPendingSubmission = useCallback(() => {
    const attempt = pendingAttemptRef.current;
    if (
      !attempt ||
      attempt.owner !== owner ||
      state.pendingSubmission?.state !== "uncertain"
    ) {
      return Promise.resolve(false);
    }
    return executeAttempt(attempt);
  }, [executeAttempt, owner, state.pendingSubmission?.state]);

  return useMemo(
    () => {
      const visibleState = state.owner === owner
        ? state
        : {
            owner,
            projection: null,
            isLoading: owner !== null,
            loadFailed: false,
            isClosed: false,
            submissionError: null,
            pendingSubmission: null,
            lastAcceptedSubmission: null,
          };

      return {
        sessionId,
        runId,
        projection: visibleState.projection,
        history: history.runs,
        historyIsLoading: history.isLoading,
        historyLoadFailed: history.loadFailed,
        historyHasMore: history.hasMore,
        loadMoreHistory: history.loadMore,
        refreshHistory: history.refresh,
        isLoading: visibleState.isLoading,
        loadFailed: visibleState.loadFailed,
        isClosed:
          !isRunActive || visibleState.isClosed ||
          (visibleState.projection !== null && visibleState.projection.state !== "open"),
        submissionError: visibleState.submissionError,
        pendingSubmission: visibleState.pendingSubmission,
        lastAcceptedSubmission: visibleState.lastAcceptedSubmission,
        submitText,
        submitAnswers,
        retryPendingSubmission,
        refresh: readSnapshot,
        retire,
      };
    },
    [
      owner,
      history,
      isRunActive,
      state,
      readSnapshot,
      retire,
      retryPendingSubmission,
      runId,
      sessionId,
      submitAnswers,
      submitText,
    ],
  );
}
