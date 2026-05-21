"use client";

import { useMemo } from "react";
import { useMe } from "@/hooks/use-me";

export type Capability =
  | "workspace.manage"
  | "members.manage"
  | "agents.manage"
  | "squads.manage"
  | "secrets.manage"
  | "sessions.create"
  | "approvals.view_all"
  | "approvals.decide_all"
  | "approvals.decide_department"
  | "approvals.decide_own"
  | "audit.view";

export interface PermissionInfo {
  role: string | null;
  departmentId: string | null;
  identityId: string | null;
  permissions: Set<string>;
  has: (cap: Capability | string) => boolean;
  /** Approval-aware predicate. Used to gate the inline approve/deny buttons. */
  canDecideApproval: (params: {
    requestedByIdentityId?: string | null;
    sessionOwnerIdentityId?: string | null;
    sessionOwnerDepartmentId?: string | null;
  }) => boolean;
}

export function usePermissions(): PermissionInfo {
  const me = useMe();
  return useMemo<PermissionInfo>(() => {
    const data = me.data;
    const perms = new Set<string>(data?.permissions ?? []);
    const departmentId = data?.current_department_id ?? null;
    const identityId = data?.id ?? null;

    const has = (cap: string) => perms.has(cap);

    const canDecideApproval: PermissionInfo["canDecideApproval"] = ({
      requestedByIdentityId,
      sessionOwnerIdentityId,
      sessionOwnerDepartmentId,
    }) => {
      if (perms.has("approvals.decide_all")) return true;
      if (
        perms.has("approvals.decide_department") &&
        departmentId &&
        sessionOwnerDepartmentId &&
        departmentId === sessionOwnerDepartmentId
      ) {
        return true;
      }
      if (perms.has("approvals.decide_own") && identityId) {
        if (requestedByIdentityId && requestedByIdentityId === identityId) return true;
        if (sessionOwnerIdentityId && sessionOwnerIdentityId === identityId) return true;
      }
      return false;
    };

    return {
      role: data?.current_role ?? null,
      departmentId,
      identityId,
      permissions: perms,
      has,
      canDecideApproval,
    };
  }, [me.data]);
}
