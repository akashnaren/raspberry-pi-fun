/** Painted before WebSocket. HDMI must never wait on a socket to show a room. */

export const FALLBACK_OFFICE = {
  walls: "#2a2118",
  floor: "tile_warm",
  palette: {
    ambient: "#1a1410",
    amber: "#F59E0B",
  },
  budget: { furniture: 2 },
  desks: [
    { owner: "mira", x: 3, y: 4, facing: "south", items: ["monitor", "coffee_mug", "plant"] },
    { owner: "nova", x: 9, y: 4, facing: "south", items: ["monitor", "second_monitor", "coffee_mug", "plant"] },
    { owner: "kessler", x: 15, y: 4, facing: "south", items: ["monitor", "coffee_mug", "notebook", "plant"] },
    { owner: "jules", x: 20, y: 7, facing: "west", items: ["monitor", "coffee_mug", "plant"] },
  ],
  rooms: [
    { name: "bullpen", x: 1, y: 1, w: 22, h: 10 },
    { name: "break room", x: 1, y: 12, w: 8, h: 5 },
    { name: "meeting", x: 10, y: 12, w: 13, h: 5 },
  ],
  decor: [
    {
      kind: "whiteboard",
      x: 2,
      y: 1,
      w: 8,
      h: 2,
      text: "SHIP: Docs print · paste-csv",
      advertises: "plan",
    },
    { kind: "window", x: 11, y: 1, w: 2, h: 1 },
    { kind: "window", x: 17, y: 1, w: 2, h: 1 },
    { kind: "window", x: 1, y: 13, w: 1, h: 2 },
    { kind: "window", x: 21, y: 13, w: 1, h: 2 },
    { kind: "lamp", x: 5, y: 3 },
    { kind: "lamp", x: 11, y: 3 },
    { kind: "lamp", x: 17, y: 3 },
    { kind: "lamp", x: 21, y: 6 },
    { kind: "plant", x: 21, y: 2 },
    { kind: "plant", x: 8, y: 9 },
    { kind: "plant", x: 1, y: 8 },
    { kind: "plant", x: 12, y: 9 },
    { kind: "clock", x: 21, y: 9 },
    { kind: "shelf", x: 14, y: 1 },
    { kind: "filing_cabinet", x: 20, y: 5 },
    { kind: "coffee", x: 2, y: 14, advertises: "hang out" },
    { kind: "couch", x: 4, y: 15, advertises: "break" },
    { kind: "rug", x: 2, y: 13 },
    { kind: "plant", x: 7, y: 13 },
    { kind: "lamp", x: 16, y: 13 },
    { kind: "plant", x: 22, y: 15 },
    { kind: "minifridge", x: 7, y: 15 },
    { kind: "table", x: 14, y: 14, advertises: "review" },
    { kind: "beanbag", x: 10, y: 13 },
  ],
};

export const FALLBACK_CAST = [
  {
    id: "nova",
    name: "Nova Chen",
    role: "programmer",
    accent: "#F97316",
    color: "#F97316",
    model: "qwen/qwen3-coder-next",
    modelFamily: "qwen",
    priorities: "Ship a usable tool tonight. Working beats pretty.",
    aesthetics: {
      skin: "warm_light",
      hair: "ponytail_dark",
      outfit: { top: "hoodie", bottom: "jeans", shoes: "sneakers", accessory: "earbuds" },
      desk_style: "messy cables + stickers",
    },
  },
  {
    id: "kessler",
    name: "Kessler Holt",
    role: "qa",
    accent: "#14B8A6",
    color: "#14B8A6",
    model: "nousresearch/hermes-3-llama-3.1-70b",
    modelFamily: "nousresearch",
    priorities: "No ugly or broken UX. Reject loose greens.",
    aesthetics: {
      skin: "cool_olive",
      hair: "cropped_silver",
      outfit: { top: "tee", bottom: "trousers", shoes: "sneakers", accessory: "glasses" },
      desk_style: "minimal dual-note pad",
    },
  },
  {
    id: "mira",
    name: "Mira Sol",
    role: "producer",
    accent: "#F59E0B",
    color: "#F59E0B",
    model: "z-ai/glm-5.3-flash",
    modelFamily: "z-ai",
    priorities: "One clear useful tool. Kill scope creep.",
    aesthetics: {
      skin: "warm_medium",
      hair: "shoulder_brown",
      outfit: { top: "blazer", bottom: "skirt", shoes: "boots", accessory: "watch" },
      desk_style: "kanban sticky wall",
    },
  },
  {
    id: "jules",
    name: "Jules Park",
    role: "office_manager",
    accent: "#6366F1",
    color: "#6366F1",
    model: "meta-llama/llama-4-scout",
    modelFamily: "meta-llama",
    priorities: "Office looks intentional. Thrift the furniture budget.",
    aesthetics: {
      skin: "warm_medium",
      hair: "short_black_wavy",
      outfit: { top: "cardigan", bottom: "chinos", shoes: "loafers", accessory: "keys" },
      desk_style: "neat labels + plant roster",
    },
  },
];

export function seedSprites(office = FALLBACK_OFFICE, employees = FALLBACK_CAST) {
  const sprites = new Map();
  for (const employee of employees) {
    const desk = (office.desks || []).find((item) => item.owner === employee.id);
    const x = desk ? desk.x + 1 : 4;
    const y = desk ? desk.y + 2 : 6;
    sprites.set(employee.id, {
      x,
      y,
      path: [],
      pose: "idle",
      facing: desk?.facing === "west" ? -1 : 1,
      frame: 0,
      at: "desk",
      active: 0,
      blinkUntil: 0,
    });
  }
  return sprites;
}
