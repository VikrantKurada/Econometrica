import {
  ChevronRight,
  Folder,
  FolderOpen,
  MessageSquare,
  MessageSquarePlus,
  Pencil,
  Trash2,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "../../lib/cn";
import type { Chat, Project } from "../../lib/types";
import { IconButton } from "../ui/IconButton";
import { InlineRename } from "./InlineRename";

/** A row in the tree, flattened enough to navigate with the arrow keys. */
export type TreeNode =
  | { kind: "project"; id: string; name: string }
  | { kind: "chat"; id: string; projectId: string; name: string };

export function nodeKey(node: TreeNode): string {
  return `${node.kind}:${node.id}`;
}

export interface TreeCallbacks {
  onSelectProject: (projectId: string) => void;
  onSelectChat: (projectId: string, chatId: string) => void;
  onToggleExpanded: (projectId: string) => void;
  onStartRename: (node: TreeNode) => void;
  onCommitRename: (node: TreeNode, name: string) => void;
  onCancelRename: () => void;
  onRequestDelete: (node: TreeNode) => void;
  onNewChat: (projectId: string) => void;
  onFocusRow: (key: string) => void;
}

interface RowProps {
  node: TreeNode;
  icon: LucideIcon;
  level: number;
  selected: boolean;
  focused: boolean;
  renaming: boolean;
  expanded?: boolean;
  callbacks: TreeCallbacks;
  /** Extra row actions, shown on hover or when the row has focus. */
  actions?: ReactNode;
  children?: ReactNode;
}

const INDENT_REM = 0.75;

/**
 * One tree row. Deliberately a single tab stop with an explicit aria-label:
 * a name computed from the row's contents would swallow the action buttons and,
 * for a project, every chat nested underneath it.
 */
function TreeRow({
  node,
  icon: Icon,
  level,
  selected,
  focused,
  renaming,
  expanded,
  callbacks,
  actions,
  children,
}: RowProps) {
  const key = nodeKey(node);
  const isProject = node.kind === "project";

  const select = (): void => {
    if (isProject) callbacks.onSelectProject(node.id);
    else callbacks.onSelectChat(node.projectId, node.id);
    callbacks.onFocusRow(key);
  };

  return (
    <li
      role="treeitem"
      aria-label={node.name}
      aria-level={level + 1}
      aria-selected={selected}
      aria-expanded={isProject ? expanded : undefined}
      aria-keyshortcuts="F2 Delete"
      data-key={key}
      tabIndex={focused ? 0 : -1}
      onFocus={(event) => {
        if (event.target === event.currentTarget) callbacks.onFocusRow(key);
      }}
      onClick={(event) => {
        event.stopPropagation();
        select();
      }}
      onDoubleClick={(event) => {
        event.stopPropagation();
        callbacks.onStartRename(node);
      }}
      className="outline-none"
    >
      <div
        style={{ paddingLeft: `${String(0.25 + level * INDENT_REM)}rem` }}
        className={cn(
          "group/row relative flex h-7 items-center gap-1 pr-1",
          "cursor-default text-sm select-none",
          selected ? "bg-accent/12 text-text-primary" : "text-text-secondary hover:bg-surface-2",
          focused && "ring-1 ring-accent/45 ring-inset",
        )}
      >
        {selected ? (
          <span aria-hidden="true" className="absolute inset-y-0 left-0 w-0.5 bg-accent" />
        ) : null}

        {isProject ? (
          <button
            type="button"
            tabIndex={-1}
            aria-label={`${expanded ? "Collapse" : "Expand"} ${node.name}`}
            onClick={(event) => {
              event.stopPropagation();
              callbacks.onToggleExpanded(node.id);
            }}
            className="inline-flex size-4 shrink-0 items-center justify-center rounded-sm text-text-secondary hover:bg-surface-2"
          >
            <ChevronRight
              size={12}
              strokeWidth={2}
              aria-hidden="true"
              className={cn("transition-transform duration-100", expanded && "rotate-90")}
            />
          </button>
        ) : (
          <span aria-hidden="true" className="size-4 shrink-0" />
        )}

        <Icon size={13} strokeWidth={1.75} aria-hidden="true" className="shrink-0 opacity-70" />

        {renaming ? (
          <InlineRename
            value={node.name}
            label={`Rename ${node.kind} ${node.name}`}
            onCommit={(next) => callbacks.onCommitRename(node, next)}
            onCancel={callbacks.onCancelRename}
          />
        ) : (
          <>
            <span className={cn("truncate", selected && "font-medium")}>{node.name}</span>
            <span
              className={cn(
                "ml-auto flex items-center gap-0.5 opacity-0 transition-opacity",
                "group-hover/row:opacity-100 group-focus-within/row:opacity-100",
                focused && "opacity-100",
              )}
            >
              {actions}
              <IconButton
                icon={Pencil}
                size="sm"
                tooltip={false}
                tabIndex={-1}
                label={`Rename ${node.name}`}
                onClick={(event) => {
                  event.stopPropagation();
                  callbacks.onStartRename(node);
                }}
              />
              <IconButton
                icon={Trash2}
                size="sm"
                variant="danger"
                tooltip={false}
                tabIndex={-1}
                label={`Delete ${node.name}`}
                onClick={(event) => {
                  event.stopPropagation();
                  callbacks.onRequestDelete(node);
                }}
              />
            </span>
          </>
        )}
      </div>

      {children}
    </li>
  );
}

interface ProjectItemProps {
  project: Project;
  chats: Chat[];
  chatsLoading: boolean;
  expanded: boolean;
  selectedProjectId: string | null;
  selectedChatId: string | null;
  focusedKey: string | null;
  renamingKey: string | null;
  callbacks: TreeCallbacks;
}

/** A project row and, when expanded, its chats. */
export function ProjectItem({
  project,
  chats,
  chatsLoading,
  expanded,
  selectedProjectId,
  selectedChatId,
  focusedKey,
  renamingKey,
  callbacks,
}: ProjectItemProps) {
  const node: TreeNode = { kind: "project", id: project.id, name: project.name };
  const key = nodeKey(node);

  return (
    <TreeRow
      node={node}
      icon={expanded ? FolderOpen : Folder}
      level={0}
      expanded={expanded}
      selected={selectedProjectId === project.id && selectedChatId === null}
      focused={focusedKey === key}
      renaming={renamingKey === key}
      callbacks={callbacks}
      actions={
        <IconButton
          icon={MessageSquarePlus}
          size="sm"
          tooltip={false}
          tabIndex={-1}
          label={`New chat in ${project.name}`}
          onClick={(event) => {
            event.stopPropagation();
            callbacks.onNewChat(project.id);
          }}
        />
      }
    >
      {expanded ? (
        <ul role="group" aria-label={`Chats in ${project.name}`}>
          {chats.map((chat) => {
            const chatNode: TreeNode = {
              kind: "chat",
              id: chat.id,
              projectId: project.id,
              name: chat.name,
            };
            return (
              <TreeRow
                key={chat.id}
                node={chatNode}
                icon={MessageSquare}
                level={1}
                selected={selectedChatId === chat.id}
                focused={focusedKey === nodeKey(chatNode)}
                renaming={renamingKey === nodeKey(chatNode)}
                callbacks={callbacks}
              />
            );
          })}
          {chats.length === 0 ? (
            <li
              role="none"
              className="py-1 pl-9 text-xs text-text-secondary/70 italic"
              // Not a treeitem: there is nothing here to select or act on.
            >
              {chatsLoading ? "Loading…" : "No chats"}
            </li>
          ) : null}
        </ul>
      ) : null}
    </TreeRow>
  );
}
