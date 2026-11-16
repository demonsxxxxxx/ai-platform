import {
  Button, Checkbox, Input, ModalBackdrop, ModalPopup, ModalPortal, ModalRoot, ModalTitle,
} from "@lobehub/ui/base-ui";
import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, ChevronRight, Folder, Plus, Search, Users, X } from "lucide-react";
import { Virtuoso } from "react-virtuoso";
import { companyEmployeesApi, type CompanyEmployee } from "../../services/api/companyEmployees";
import { capabilityDistributionApi, type DepartmentDirectoryNode } from "../../services/api/capabilityDistribution";
import type { AdminModelGroupMember } from "../../services/api/modelGroups";
import { flattenDepartmentDirectory } from "./departmentDirectorySelection";

const PAGE_SIZE = 100;
const MAX_MEMBERS = 10000;
const memberId = (member: AdminModelGroupMember) => member.work_id || member.user_id;

interface MemberPickerProps {
  members: AdminModelGroupMember[];
  disabled: boolean;
  onConfirm: (members: AdminModelGroupMember[]) => void;
}

function DepartmentBranch({ nodes, selectedId, disabled, onSelect }: {
  nodes: DepartmentDirectoryNode[];
  selectedId: string;
  disabled: boolean;
  onSelect: (id: string) => void;
}) {
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  return <ul className="model-member-picker-tree-list">
    {nodes.map((node) => <li key={node.directoryId}>
      <div className="model-member-picker-tree-row" data-selected={node.directoryId === selectedId || undefined}>
        {node.children.length > 0 ? <Button type="text" htmlType="button" size="small"
          aria-label={`${collapsed.has(node.directoryId) ? "展开" : "收起"}${node.name}`}
          aria-expanded={!collapsed.has(node.directoryId)} disabled={disabled}
          icon={collapsed.has(node.directoryId) ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
          onClick={() => setCollapsed((current) => {
            const next = new Set(current);
            if (next.has(node.directoryId)) next.delete(node.directoryId); else next.add(node.directoryId);
            return next;
          })} /> : <Folder size={14} aria-hidden="true" className="model-member-picker-folder" />}
        <Button type="text" htmlType="button" className="model-member-picker-department"
          disabled={disabled || !node.selectable} aria-pressed={selectedId === node.directoryId}
          aria-label={node.path} title={node.path} onClick={() => onSelect(node.directoryId)}>
          <span>{node.name}</span>
        </Button>
      </div>
      {node.children.length > 0 && !collapsed.has(node.directoryId) ? <DepartmentBranch nodes={node.children}
        selectedId={selectedId} disabled={disabled} onSelect={onSelect} /> : null}
    </li>)}
  </ul>;
}

/** Stages concrete employees; the group editor still owns saving and migration confirmation. */
export function ModelGroupMemberPicker({ members, disabled, onConfirm }: MemberPickerProps) {
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<AdminModelGroupMember[]>([]);
  const [query, setQuery] = useState("");
  const [departmentId, setDepartmentId] = useState("");
  const [departments, setDepartments] = useState<DepartmentDirectoryNode[] | null>(null);
  const [departmentError, setDepartmentError] = useState(false);
  const [departmentRetry, setDepartmentRetry] = useState(0);
  const [employees, setEmployees] = useState<CompanyEmployee[]>([]);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [retry, setRetry] = useState(0);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const search = query.trim();
  const scopeDepartment = search ? "" : departmentId;

  useEffect(() => {
    if (!open || disabled || departments) return;
    const controller = new AbortController();
    setDepartmentError(false);
    void capabilityDistributionApi.departmentDirectory({ signal: controller.signal }).then((value) => {
      if (!controller.signal.aborted) setDepartments(value);
    }).catch(() => { if (!controller.signal.aborted) setDepartmentError(true); });
    return () => controller.abort();
  }, [open, disabled, departments, departmentRetry]);

  useEffect(() => {
    if (!open || disabled) return;
    const controller = new AbortController();
    setStatus("loading");
    setEmployees([]);
    // Resolve every page before enabling selection, so bulk selection never means only the loaded page.
    void (async () => {
      try {
        const found = new Map<string, CompanyEmployee>();
        let offset = 0;
        let total: number | undefined;
        let snapshotId: string | undefined;
        do {
          const page = await companyEmployeesApi.list({ search: search || undefined, departmentId: scopeDepartment || undefined,
            offset, limit: PAGE_SIZE, signal: controller.signal });
          if (controller.signal.aborted) return;
          if (typeof page.snapshot_id !== "string" || !page.snapshot_id ||
              page.offset !== offset || (snapshotId !== undefined && page.snapshot_id !== snapshotId) ||
              (total !== undefined && page.total !== total) ||
              (page.employees.length === 0 && offset < page.total)) throw new Error("employee_directory_changed");
          total = page.total;
          snapshotId = page.snapshot_id;
          for (const employee of page.employees) found.set(employee.work_id, employee);
          offset += page.employees.length;
        } while (offset < total);
        if (found.size !== total) throw new Error("employee_directory_changed");
        setEmployees([...found.values()]);
        setStatus("ready");
      } catch {
        if (!controller.signal.aborted) setStatus("error");
      }
    })();
    return () => controller.abort();
  }, [open, disabled, search, scopeDepartment, retry]);

  const departmentNames = useMemo(() => new Map((departments ? flattenDepartmentDirectory(departments) : [])
    .filter((node) => node.selectable).map((node) => [node.directoryId, node.path])), [departments]);
  const selectedIds = useMemo(() => new Set(selected.map(memberId)), [selected]);
  const selectedInScope = employees.filter((employee) => selectedIds.has(employee.work_id)).length;
  const allSelected = employees.length > 0 && selectedInScope === employees.length;
  const toggleMembers = (candidates: CompanyEmployee[], checked: boolean) => {
    setSelected((current) => {
      const next = new Map(current.map((member) => [memberId(member), member]));
      for (const employee of candidates) {
        if (checked) {
          if (!next.has(employee.work_id)) next.set(employee.work_id, {
            user_id: "", work_id: employee.work_id, display_name: employee.display_name, is_primary: true,
          });
        } else next.delete(employee.work_id);
      }
      return [...next.values()];
    });
  };
  const chooseDepartment = (id: string) => { setDepartmentId(id); setQuery(""); };
  const close = () => setOpen(false);

  return <>
    <Button ref={triggerRef} type="default" htmlType="button" disabled={disabled} aria-label="按组织架构选择人员"
      icon={<Plus size={15} />} onClick={() => { setSelected([...members]); setQuery(""); setOpen(true); }}>添加成员</Button>
    <ModalRoot open={open && !disabled} onOpenChange={(value) => { if (!value) close(); }}>
      <ModalPortal><ModalBackdrop />
        <ModalPopup width={830} panelClassName="model-member-picker-panel" finalFocus={triggerRef} initialFocus={searchRef}
          aria-label="公司组织架构选人" onKeyDown={(event) => {
            if (event.key === "Escape") { event.stopPropagation(); close(); }
          }}>
          <div className="model-member-picker-header"><ModalTitle className="model-member-picker-title">添加成员</ModalTitle>
            <Button type="text" htmlType="button" aria-label="关闭选人弹窗" onClick={close} icon={<X size={18} />} /></div>
          <div className="model-member-picker-body">
            <nav className="model-member-picker-tree" aria-label="组织架构">
              <h3>组织架构</h3>
              <div className="model-member-picker-tree-row" data-selected={!departmentId || undefined}>
                <Users size={15} aria-hidden="true" className="model-member-picker-folder" />
                <Button type="text" htmlType="button" className="model-member-picker-department" aria-pressed={!departmentId}
                  onClick={() => chooseDepartment("")}>全部部门</Button>
              </div>
              {departments ? <DepartmentBranch nodes={departments} selectedId={departmentId} disabled={disabled} onSelect={chooseDepartment} /> :
                departmentError ? <div role="alert" className="model-member-picker-directory-error"><p>组织架构加载失败</p>
                  <Button htmlType="button" size="small" onClick={() => setDepartmentRetry((value) => value + 1)}>重试组织架构</Button></div> :
                  <p role="status" className="model-member-picker-status">正在加载组织架构…</p>}
            </nav>
            <div className="model-member-picker-people">
              <Input ref={searchRef} aria-label="搜索公司员工" prefix={<Search size={15} />} placeholder="全公司搜索姓名或工号" maxLength={128}
                value={query} disabled={disabled} onChange={(event) => setQuery(event.target.value)} />
              <div className="model-member-picker-bulk">
                <Checkbox aria-label={search ? "全选搜索结果" : departmentId ? "全选本部门" : "全选全部人员"}
                  checked={allSelected} indeterminate={selectedInScope > 0 && !allSelected}
                  disabled={disabled || status !== "ready" || !employees.length}
                  onChange={(checked) => toggleMembers(employees, checked)}>
                  {search ? "全选搜索结果" : departmentId ? "全选本部门" : "全选全部人员"}
                </Checkbox>
                <span aria-live="polite">已选 {selectedInScope} / {employees.length} 人</span>
              </div>
              <div className="model-member-picker-directory" data-model-group-member-directory data-employee-total={employees.length} aria-busy={status === "loading"}>
                {status === "loading" ? <p role="status" className="model-member-picker-status">正在加载成员目录…</p> : null}
                {status === "error" ? <div role="alert" className="model-member-picker-directory-error"><p>公司员工目录加载失败，请重试。</p>
                  <Button htmlType="button" size="small" onClick={() => setRetry((value) => value + 1)}>重试</Button></div> : null}
                {status === "ready" && employees.length === 0 ? <p className="model-member-picker-status">没有匹配的公司员工</p> : null}
                {status === "ready" && employees.length > 0 ? <Virtuoso key={`${search}:${scopeDepartment}`} className="model-member-picker-list"
                  data={employees} computeItemKey={(_, employee) => employee.work_id} initialItemCount={Math.min(8, employees.length)} increaseViewportBy={100}
                  itemContent={(_, employee) => <div className="model-member-picker-person"
                  data-selected={selectedIds.has(employee.work_id) || undefined}>
                  <Checkbox aria-label={`选择成员 ${employee.display_name}`} checked={selectedIds.has(employee.work_id)}
                    disabled={disabled || status !== "ready"} className="model-member-picker-person-control"
                    classNames={{ text: "model-member-picker-person-content" }}
                    onChange={(checked) => toggleMembers([employee], checked)}>
                    <span className="model-member-picker-avatar" aria-hidden="true">{employee.display_name.slice(0, 1)}</span>
                    <span className="model-member-picker-identity"><span>{employee.display_name}</span>
                      <span className="model-member-picker-department-path">{(employee.department_ids ?? []).map((id) => departmentNames.get(id)).filter(Boolean).join("、")}</span>
                    </span>
                    <span className="model-member-picker-work-id">{employee.work_id}</span>
                  </Checkbox>
                </div>} /> : null}
              </div>
            </div>
          </div>
          <div className="model-member-picker-selected"><strong aria-live="polite">已选 {selected.length} 人</strong>
            <div className="model-member-picker-chips">{selected.length > 0 ? <Virtuoso horizontalDirection className="model-member-picker-chip-list"
              data={selected} computeItemKey={(_, member) => memberId(member)} initialItemCount={Math.min(8, selected.length)}
              itemContent={(_, member) => <Button type="default" size="small" htmlType="button"
                aria-label={`移除已选 ${member.display_name || memberId(member)}`} title={`${member.display_name || ""} ${memberId(member)}`}
                onClick={() => setSelected((current) => current.filter((item) => memberId(item) !== memberId(member)))}>
                {member.display_name || memberId(member)}<X size={12} aria-hidden="true" />
              </Button>} /> : null}</div>
            <Button type="text" size="small" htmlType="button" disabled={selected.length === 0} onClick={() => setSelected([])}>清空</Button>
          </div>
          {selected.length > MAX_MEMBERS ? <p role="alert" className="model-member-picker-limit">每组最多添加 {MAX_MEMBERS} 人，请减少选择。</p> : null}
          <div className="model-member-picker-footer"><Button htmlType="button" onClick={close}>取消</Button>
            <Button type="primary" htmlType="button" disabled={disabled || selected.length > MAX_MEMBERS}
              onClick={() => { onConfirm(selected); close(); }}>添加 {selected.length} 人</Button></div>
        </ModalPopup>
      </ModalPortal>
    </ModalRoot>
  </>;
}
