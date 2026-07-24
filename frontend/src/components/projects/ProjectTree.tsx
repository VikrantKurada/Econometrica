import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { FolderTree, Plus, TriangleAlert, X } from "lucide-react";
import { useMemo, useRef, useState, type KeyboardEvent } from "react";

import { api } from "../../lib/api";
import { queryKeys } from "../../lib/queryClient";
import { useSelectionStore } from "../../lib/store";
import type { Chat, Project } from "../../lib/types";
import { EmptyState } from "../layout/EmptyState";
import { PaneHeader } from "../layout/PaneHeader";
import { ConfirmDialog } from "../ui/ConfirmDialog";
import { IconButton } from "../ui/IconButton";
import { ProjectItem, nodeKey, type TreeCallbacks, type TreeNode } from "./ProjectItem";

const NEW_PROJECT_NAME = "Untitled project";
const NEW_CHAT_NAME = "New chat";

/**
 * The left pane: every project, and the chats of whichever projects are open.
 *
 * Server state is TanStack Query's; which rows are selected, expanded, being
 * renamed or focused is the UI's. Chats are fetched per expanded project, so an
 * unopened project costs nothing.
 */
export function ProjectTree() {
  const queryClient = useQueryClient();
  const treeRef = useRef<HTMLUListElement>(null);

  const selectedProjectId = useSelectionStore((state) => state.selectedProjectId);
  const selectedChatId = useSelectionStore((state) => state.selectedChatId);
  const expandedProjectIds = useSelectionStore((state) => state.expandedProjectIds);
  const selectProject = useSelectionStore((state) => state.selectProject);
  const selectChat = useSelectionStore((state) => state.selectChat);
  const setProjectExpanded = useSelectionStore((state) => state.setProjectExpanded);
  const toggleProjectExpanded = useSelectionStore((state) => state.toggleProjectExpanded);
  const forgetProject = useSelectionStore((state) => state.forgetProject);
  const forgetChat = useSelectionStore((state) => state.forgetChat);

  const [error, setError] = useState<string | null>(null);
  const [renamingKey, setRenamingKey] = useState<string | null>(null);
  const [focusedKey, setFocusedKey] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<TreeNode | null>(null);

  const projectsQuery = useQuery({
    queryKey: queryKeys.projects,
    queryFn: () => api.listProjects(),
  });
  const projects = useMemo<Project[]>(() => projectsQuery.data ?? [], [projectsQuery.data]);

  // Only expanded projects get a chats query at all — that is what makes the
  // load lazy rather than a fan-out on mount.
  const openProjectIds = useMemo(
    () => projects.filter((project) => expandedProjectIds.includes(project.id)).map((p) => p.id),
    [projects, expandedProjectIds],
  );

  const chatQueries = useQueries({
    queries: openProjectIds.map((projectId) => ({
      queryKey: queryKeys.chats(projectId),
      queryFn: () => api.listChats(projectId),
    })),
  });

  const chatsByProject = useMemo(() => {
    const map = new Map<string, { chats: Chat[]; loading: boolean }>();
    openProjectIds.forEach((projectId, index) => {
      const query = chatQueries[index];
      map.set(projectId, { chats: query?.data ?? [], loading: query?.isPending ?? false });
    });
    return map;
  }, [openProjectIds, chatQueries]);

  /** Rows in the order they appear on screen; the basis for arrow navigation. */
  const visibleNodes = useMemo<TreeNode[]>(() => {
    const nodes: TreeNode[] = [];
    for (const project of projects) {
      nodes.push({ kind: "project", id: project.id, name: project.name });
      if (!expandedProjectIds.includes(project.id)) continue;
      for (const chat of chatsByProject.get(project.id)?.chats ?? []) {
        nodes.push({ kind: "chat", id: chat.id, projectId: project.id, name: chat.name });
      }
    }
    return nodes;
  }, [projects, expandedProjectIds, chatsByProject]);

  const activeKey = focusedKey ?? (visibleNodes[0] ? nodeKey(visibleNodes[0]) : null);

  const focusRow = (key: string): void => {
    setFocusedKey(key);
    treeRef.current?.querySelector<HTMLElement>(`[data-key="${key}"]`)?.focus();
  };

  const reportError = (cause: unknown): void => {
    setError(cause instanceof Error ? cause.message : "Something went wrong");
  };

  const invalidateProjects = (): Promise<void> =>
    queryClient.invalidateQueries({ queryKey: queryKeys.projects });

  const invalidateChats = (projectId: string): Promise<void> =>
    queryClient.invalidateQueries({ queryKey: queryKeys.chats(projectId) });

  const createProject = useMutation({
    mutationFn: () => api.createProject({ name: NEW_PROJECT_NAME }),
    onSuccess: async (project) => {
      setError(null);
      await invalidateProjects();
      selectProject(project.id);
      // Straight into naming it: an "Untitled project" left lying around is a
      // worse default than one extra keystroke.
      setRenamingKey(`project:${project.id}`);
      setFocusedKey(`project:${project.id}`);
    },
    onError: reportError,
  });

  const createChat = useMutation({
    mutationFn: (projectId: string) => api.createChat(projectId, { name: NEW_CHAT_NAME }),
    onSuccess: async (chat) => {
      setError(null);
      setProjectExpanded(chat.project_id, true);
      await invalidateChats(chat.project_id);
      selectChat(chat.project_id, chat.id);
      setRenamingKey(`chat:${chat.id}`);
      setFocusedKey(`chat:${chat.id}`);
    },
    onError: reportError,
  });

  const renameNode = useMutation<Project | Chat, Error, { node: TreeNode; name: string }>({
    mutationFn: ({ node, name }) =>
      node.kind === "project"
        ? api.updateProject(node.id, { name })
        : api.updateChat(node.id, { name }),
    onSuccess: async (_result, { node }) => {
      setError(null);
      setRenamingKey(null);
      if (node.kind === "project") await invalidateProjects();
      else await invalidateChats(node.projectId);
    },
    onError: (cause) => {
      // Leave the row showing the name the server still holds, and say why.
      setRenamingKey(null);
      reportError(cause);
    },
  });

  const deleteNode = useMutation({
    mutationFn: (node: TreeNode) =>
      node.kind === "project" ? api.deleteProject(node.id) : api.deleteChat(node.id),
    onSuccess: async (_result, node) => {
      setError(null);
      setPendingDelete(null);
      if (node.kind === "project") {
        forgetProject(node.id);
        await invalidateProjects();
      } else {
        forgetChat(node.id);
        await invalidateChats(node.projectId);
      }
    },
    onError: (cause) => {
      setPendingDelete(null);
      reportError(cause);
    },
  });

  const callbacks: TreeCallbacks = {
    onSelectProject: selectProject,
    onSelectChat: selectChat,
    onToggleExpanded: toggleProjectExpanded,
    onStartRename: (node) => {
      setRenamingKey(nodeKey(node));
      setFocusedKey(nodeKey(node));
    },
    onCommitRename: (node, name) => renameNode.mutate({ node, name }),
    onCancelRename: () => setRenamingKey(null),
    onRequestDelete: (node) => setPendingDelete(node),
    onNewChat: (projectId) => createChat.mutate(projectId),
    onFocusRow: setFocusedKey,
  };

  function handleKeyDown(event: KeyboardEvent<HTMLUListElement>): void {
    if (renamingKey !== null) return;

    const index = visibleNodes.findIndex((node) => nodeKey(node) === activeKey);
    const node = visibleNodes[index];
    if (!node) return;

    const move = (target: number): void => {
      const next = visibleNodes[Math.max(0, Math.min(visibleNodes.length - 1, target))];
      if (next) focusRow(nodeKey(next));
    };

    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        move(index + 1);
        return;
      case "ArrowUp":
        event.preventDefault();
        move(index - 1);
        return;
      case "Home":
        event.preventDefault();
        move(0);
        return;
      case "End":
        event.preventDefault();
        move(visibleNodes.length - 1);
        return;
      case "ArrowRight":
        event.preventDefault();
        if (node.kind === "project") {
          if (expandedProjectIds.includes(node.id)) move(index + 1);
          else setProjectExpanded(node.id, true);
        }
        return;
      case "ArrowLeft":
        event.preventDefault();
        if (node.kind === "chat") {
          focusRow(`project:${node.projectId}`);
        } else if (expandedProjectIds.includes(node.id)) {
          setProjectExpanded(node.id, false);
        }
        return;
      case "Enter":
      case " ":
        event.preventDefault();
        if (node.kind === "project") selectProject(node.id);
        else selectChat(node.projectId, node.id);
        return;
      case "F2":
        event.preventDefault();
        callbacks.onStartRename(node);
        return;
      case "Delete":
        event.preventDefault();
        setPendingDelete(node);
        return;
      default:
    }
  }

  return (
    <div className="flex h-full flex-col">
      <PaneHeader
        title="Projects"
        actions={
          <IconButton
            icon={Plus}
            label="New project"
            disabled={createProject.isPending}
            onClick={() => createProject.mutate()}
          />
        }
      />

      {error !== null ? (
        <div
          role="alert"
          className="flex items-start gap-2 border-b border-negative/30 bg-negative/10 px-3 py-2 text-xs text-text-primary"
        >
          <TriangleAlert size={13} className="mt-px shrink-0 text-negative" aria-hidden="true" />
          <span className="min-w-0 flex-1 break-words">{error}</span>
          <IconButton
            icon={X}
            size="sm"
            tooltip={false}
            label="Dismiss error"
            onClick={() => setError(null)}
          />
        </div>
      ) : null}

      <div className="scroll-thin min-h-0 flex-1 overflow-y-auto py-1">
        {projectsQuery.isPending ? (
          <p className="px-3 py-2 text-xs text-text-secondary">Loading projects…</p>
        ) : projectsQuery.isError ? (
          <p className="px-3 py-2 text-xs text-negative">
            Could not load projects. Is the backend running?
          </p>
        ) : projects.length === 0 ? (
          <EmptyState
            icon={FolderTree}
            title="No projects yet"
            hint="A project holds the chats and artifacts for one line of analysis."
          />
        ) : (
          <ul
            ref={treeRef}
            role="tree"
            aria-label="Projects and chats"
            onKeyDown={handleKeyDown}
            className="outline-none"
          >
            {projects.map((project) => (
              <ProjectItem
                key={project.id}
                project={project}
                chats={chatsByProject.get(project.id)?.chats ?? []}
                chatsLoading={chatsByProject.get(project.id)?.loading ?? false}
                expanded={expandedProjectIds.includes(project.id)}
                selectedProjectId={selectedProjectId}
                selectedChatId={selectedChatId}
                focusedKey={activeKey}
                renamingKey={renamingKey}
                callbacks={callbacks}
              />
            ))}
          </ul>
        )}
      </div>

      <ConfirmDialog
        open={pendingDelete !== null}
        onOpenChange={(open) => {
          if (!open) setPendingDelete(null);
        }}
        destructive
        title={pendingDelete?.kind === "chat" ? "Delete chat" : "Delete project"}
        description={
          pendingDelete?.kind === "chat" ? (
            <>
              <span className="font-medium text-text-primary">{pendingDelete.name}</span> and its
              messages will be removed. This cannot be undone.
            </>
          ) : (
            <>
              <span className="font-medium text-text-primary">{pendingDelete?.name}</span> and every
              chat inside it will be removed. This cannot be undone.
            </>
          )
        }
        confirmLabel="Delete"
        onConfirm={() => {
          if (pendingDelete) deleteNode.mutate(pendingDelete);
        }}
      />
    </div>
  );
}
