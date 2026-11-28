import type { MetroGraphStation } from "./types";

export function edgeCurve(
  from: MetroGraphStation,
  to: MetroGraphStation,
): string {
  const mx = (from.x + to.x) / 2;
  const my = (from.y + to.y) / 2;
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const cx = mx - dy * 0.12;
  const cy = my + dx * 0.12;
  return `M ${from.x} ${from.y} Q ${cx} ${cy} ${to.x} ${to.y}`;
}

export const LINE_BACKBONES: { color: string; points: string; dashed?: boolean }[] = [
  { color: "#8D8D8D", points: "50,52 310,52" },
  { color: "#6EC4E8", points: "50,140 310,140" },
  { color: "#007852", points: "330,222 880,222" },
  { color: "#FFCD00", points: "32,360 318,360" },
  { color: "#FF7E2E", points: "358,266 358,360" },
  { color: "#003CA6", points: "402,360 532,360" },
  { color: "#CF009E", points: "555,360 698,360" },
  { color: "#C9910D", points: "800,465 875,540", dashed: false },
  { color: "#704B1C", points: "445,480 645,480" },
  { color: "#837902", points: "150,610 380,610" },
  { color: "#62259D", points: "440,690 725,690" },
  { color: "#FA9ABA", points: "70,760 380,760", dashed: true },
];