import type { ComponentProps } from "react";

// shadcn/ui's native table composition; callers retain their layout and cell styles.
export function Table(props: ComponentProps<"table">) {
  return <table data-slot="table" {...props} />;
}

export function TableHeader(props: ComponentProps<"thead">) {
  return <thead data-slot="table-header" {...props} />;
}

export function TableBody(props: ComponentProps<"tbody">) {
  return <tbody data-slot="table-body" {...props} />;
}

export function TableRow(props: ComponentProps<"tr">) {
  return <tr data-slot="table-row" {...props} />;
}

export function TableHead({ scope = "col", ...props }: ComponentProps<"th">) {
  return <th data-slot="table-head" scope={scope} {...props} />;
}

export function TableCell(props: ComponentProps<"td">) {
  return <td data-slot="table-cell" {...props} />;
}
