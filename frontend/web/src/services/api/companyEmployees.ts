import { authFetch } from "./fetch";

export interface CompanyEmployee {
  work_id: string;
  display_name: string;
}

export interface CompanyEmployeeList {
  employees: CompanyEmployee[];
  total: number;
  offset: number;
  limit: number;
}

export interface CompanyEmployeeQuery {
  search?: string;
  offset?: number;
  limit?: number;
  signal?: AbortSignal;
}

export const companyEmployeesApi = {
  list(options: CompanyEmployeeQuery = {}): Promise<CompanyEmployeeList> {
    const params = new URLSearchParams({
      offset: String(options.offset ?? 0), limit: String(options.limit ?? 50),
    });
    const search = options.search?.trim();
    if (search) params.set("search", search);
    return authFetch<CompanyEmployeeList>(`/api/ai/admin/company-employees?${params}`, {
      method: "GET", ...(options.signal ? { signal: options.signal } : {}),
    });
  },
};
