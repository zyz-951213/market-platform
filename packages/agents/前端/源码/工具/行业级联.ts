import type { SectorTreeNode } from "@/类型/接口";
import { fmtStockName } from "@/工具/格式化";

export interface SelectOption {
  label: string;
  value: string;
}

export function sectorOptions(tree: SectorTreeNode[], names: string[]): SelectOption[] {
  let nodes = tree;
  for (const name of names) {
    if (!name) break;
    const hit = nodes.find((n) => n.name === name);
    nodes = hit?.children || [];
  }
  return nodes.map((n) => ({ label: `${n.name} (${n.count ?? 0})`, value: n.name }));
}

export function singleOptionValue(options: SelectOption[]): string {
  return options.length === 1 ? options[0].value : "";
}

/** 默认选第一项（用于进入三级窗口）；无选项时返回空串 */
export function firstOptionValue(options: SelectOption[]): string {
  return options[0]?.value || "";
}

export function symbolOptions(
  tree: SectorTreeNode[],
  l1: string,
  l2: string,
  l3: string,
): SelectOption[] {
  const l1n = tree.find((n) => n.name === l1);
  const l2n = l1n?.children?.find((n) => n.name === l2);
  const l3n = l2n?.children?.find((n) => n.name === l3);
  return (l3n?.children || [])
    .map((s) => ({
      label: fmtStockName(s),
      value: s.symbol || "",
    }))
    .filter((o) => o.value);
}
