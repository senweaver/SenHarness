"use client";

import { useMemo, useState } from "react";
import {
  IconBuildingCommunity,
  IconChevronDown,
  IconChevronRight,
  IconCornerDownRight,
  IconLoader2,
  IconPencil,
  IconPlus,
  IconTrash,
  IconUsers,
} from "@tabler/icons-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PageHeader } from "@/components/ui/page-header";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useCreateDepartment,
  useDeleteDepartment,
  useDepartments,
  useUpdateDepartment,
} from "@/hooks/use-departments";
import { usePermissions } from "@/hooks/use-permissions";
import type { DepartmentRead } from "@/types/api";

const ROOT = "__root__";

interface TreeNode {
  dept: DepartmentRead;
  children: TreeNode[];
}

function buildTree(list: DepartmentRead[]): TreeNode[] {
  const byId = new Map<string, TreeNode>();
  list.forEach((d) => byId.set(d.id, { dept: d, children: [] }));
  const roots: TreeNode[] = [];
  byId.forEach((node) => {
    const pid = node.dept.parent_id;
    if (pid && byId.has(pid)) {
      byId.get(pid)!.children.push(node);
    } else {
      roots.push(node);
    }
  });
  const sortChildren = (nodes: TreeNode[]) => {
    nodes.sort((a, b) => a.dept.name.localeCompare(b.dept.name));
    nodes.forEach((n) => sortChildren(n.children));
  };
  sortChildren(roots);
  return roots;
}

export default function DepartmentsPage() {
  const t = useTranslations("settings.departments");
  const { data = [], isLoading } = useDepartments();
  const perms = usePermissions();
  const canManage = perms.has("members.manage");
  const tree = useMemo(() => buildTree(data), [data]);

  return (
    <div>
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={
          canManage ? <NewDeptDialog departments={data} /> : undefined
        }
      />

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <IconBuildingCommunity className="size-4 text-blue-500" />
            {t("allDepartments", { count: data.length })}
            {isLoading && <IconLoader2 className="size-3 animate-spin" />}
          </CardTitle>
          <CardDescription>{t("treeHint")}</CardDescription>
        </CardHeader>
        <CardContent>
          {isLoading && <Skeleton className="h-32" />}
          {!isLoading && data.length === 0 && (
            <p className="py-6 text-center text-xs sh-muted">
              {canManage ? t("emptyCanManage") : t("emptyReadOnly")}
            </p>
          )}
          {!isLoading && tree.length > 0 && (
            <ul className="space-y-0.5">
              {tree.map((node) => (
                <TreeRow
                  key={node.dept.id}
                  node={node}
                  depth={0}
                  allDepts={data}
                  canManage={canManage}
                />
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function TreeRow({
  node,
  depth,
  allDepts,
  canManage,
}: {
  node: TreeNode;
  depth: number;
  allDepts: DepartmentRead[];
  canManage: boolean;
}) {
  const t = useTranslations("settings.departments");
  const [expanded, setExpanded] = useState(true);
  const [editing, setEditing] = useState(false);
  const hasChildren = node.children.length > 0;

  return (
    <li>
      <div
        className="group flex items-center gap-1 rounded px-1.5 py-1 hover:bg-black/5 dark:hover:bg-white/5"
        style={{ paddingLeft: `${depth * 16 + 6}px` }}
      >
        {hasChildren ? (
          <button
            onClick={() => setExpanded((e) => !e)}
            className="size-5 shrink-0 rounded hover:bg-black/10 dark:hover:bg-white/10"
            aria-label={expanded ? t("collapse") : t("expand")}
          >
            {expanded ? (
              <IconChevronDown className="size-3.5" />
            ) : (
              <IconChevronRight className="size-3.5" />
            )}
          </button>
        ) : (
          <span className="inline-block w-5 shrink-0">
            {depth > 0 && <IconCornerDownRight className="size-3.5 sh-muted" />}
          </span>
        )}

        <IconBuildingCommunity className="size-3.5 shrink-0 text-blue-500" />

        {editing ? (
          <InlineRename
            dept={node.dept}
            allDepts={allDepts}
            onDone={() => setEditing(false)}
          />
        ) : (
          <>
            <span className="flex-1 truncate text-sm">{node.dept.name}</span>
            <span className="inline-flex items-center gap-0.5 rounded bg-black/5 px-1.5 py-0.5 text-[10px] sh-muted dark:bg-white/10">
              <IconUsers className="size-3" />
              {node.dept.member_count}
            </span>
            <span
              className="hidden truncate font-mono text-[10px] sh-muted md:inline-block md:max-w-[240px]"
              title={node.dept.path}
            >
              {node.dept.path}
            </span>
            {canManage && (
              <div className="ml-1 hidden items-center gap-0.5 group-hover:flex">
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-6"
                  onClick={() => setEditing(true)}
                  aria-label={t("rename")}
                  title={t("rename")}
                >
                  <IconPencil className="size-3" />
                </Button>
                <DeleteButton dept={node.dept} hasChildren={hasChildren} />
              </div>
            )}
          </>
        )}
      </div>

      {hasChildren && expanded && (
        <ul className="space-y-0.5">
          {node.children.map((child) => (
            <TreeRow
              key={child.dept.id}
              node={child}
              depth={depth + 1}
              allDepts={allDepts}
              canManage={canManage}
            />
          ))}
        </ul>
      )}
    </li>
  );
}

function InlineRename({
  dept,
  allDepts,
  onDone,
}: {
  dept: DepartmentRead;
  allDepts: DepartmentRead[];
  onDone: () => void;
}) {
  const t = useTranslations("settings.departments");
  const update = useUpdateDepartment();
  const [name, setName] = useState(dept.name);
  const [parentId, setParentId] = useState<string>(dept.parent_id ?? ROOT);

  // Disallow picking self or any descendant as the new parent (would create
  // a cycle in the tree).
  const forbiddenParents = useMemo(() => {
    const forbidden = new Set<string>([dept.id]);
    const children = allDepts.filter((d) => d.path.startsWith(dept.path + "/"));
    children.forEach((d) => forbidden.add(d.id));
    return forbidden;
  }, [allDepts, dept.id, dept.path]);

  const submit = async () => {
    try {
      await update.mutateAsync({
        id: dept.id,
        name: name.trim() || undefined,
        parent_id:
          parentId === ROOT
            ? null
            : parentId === (dept.parent_id ?? ROOT)
              ? undefined
              : parentId,
      });
      toast.success(t("renamed"));
      onDone();
    } catch {
      toast.error(t("renameFailed"));
    }
  };

  return (
    <div className="flex flex-1 items-center gap-1">
      <Input
        autoFocus
        className="h-7 text-xs"
        value={name}
        onChange={(e) => setName(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") void submit();
          if (e.key === "Escape") onDone();
        }}
      />
      <Select value={parentId} onValueChange={setParentId}>
        <SelectTrigger className="h-7 w-[160px] text-xs">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ROOT}>{t("rootLevel")}</SelectItem>
          {allDepts
            .filter((d) => !forbiddenParents.has(d.id))
            .map((d) => (
              <SelectItem key={d.id} value={d.id}>
                {d.path}
              </SelectItem>
            ))}
        </SelectContent>
      </Select>
      <Button
        size="sm"
        className="h-7 px-2"
        onClick={submit}
        disabled={update.isPending || !name.trim()}
      >
        {update.isPending && <IconLoader2 className="size-3 animate-spin" />}
        OK
      </Button>
      <Button
        size="sm"
        variant="ghost"
        className="h-7 px-2"
        onClick={onDone}
      >
        ✕
      </Button>
    </div>
  );
}

function DeleteButton({
  dept,
  hasChildren,
}: {
  dept: DepartmentRead;
  hasChildren: boolean;
}) {
  const t = useTranslations("settings.departments");
  const del = useDeleteDepartment();
  const onDelete = async () => {
    if (hasChildren) {
      toast.error(t("cannotDeleteNonEmpty"));
      return;
    }
    if (!confirm(t("confirmDelete", { name: dept.name }))) return;
    try {
      await del.mutateAsync(dept.id);
      toast.success(t("deleted"));
    } catch {
      toast.error(t("deleteFailed"));
    }
  };
  return (
    <Button
      variant="ghost"
      size="icon"
      className="size-6"
      onClick={onDelete}
      aria-label={t("delete")}
      title={hasChildren ? t("cannotDeleteNonEmpty") : t("delete")}
      disabled={hasChildren}
    >
      <IconTrash className="size-3" />
    </Button>
  );
}

function NewDeptDialog({ departments }: { departments: DepartmentRead[] }) {
  const t = useTranslations("settings.departments");
  const create = useCreateDepartment();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [parentId, setParentId] = useState<string>(ROOT);

  const submit = async () => {
    if (!name.trim()) return;
    try {
      await create.mutateAsync({
        name: name.trim(),
        parent_id: parentId === ROOT ? null : parentId,
      });
      toast.success(t("created"));
      setOpen(false);
      setName("");
      setParentId(ROOT);
    } catch {
      toast.error(t("createFailed"));
    }
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm">
          <IconPlus className="size-4" /> {t("new")}
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-[420px]">
        <DialogHeader>
          <DialogTitle>{t("new")}</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div className="grid gap-1.5">
            <Label htmlFor="dept-name">{t("nameLabel")}</Label>
            <Input
              id="dept-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={t("namePlaceholder")}
              autoFocus
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="dept-parent">{t("parentLabel")}</Label>
            <Select value={parentId} onValueChange={setParentId}>
              <SelectTrigger id="dept-parent">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ROOT}>{t("rootLevel")}</SelectItem>
                {departments.map((d) => (
                  <SelectItem key={d.id} value={d.id}>
                    {d.path}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => setOpen(false)}>
            {t("cancel")}
          </Button>
          <Button
            onClick={submit}
            disabled={!name.trim() || create.isPending}
          >
            {create.isPending && (
              <IconLoader2 className="size-3 animate-spin" />
            )}
            {t("save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
