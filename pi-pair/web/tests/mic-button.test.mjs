import fs from "fs";
import {
  MIC_OFF_CLASS,
  MIC_OFF_COLOR,
  MIC_ON_CLASS,
  MIC_ON_COLOR,
  MIC_ON_FILL,
  MIC_ON_HOVER_FILL,
  micStateClasses,
  paintMicButton,
} from "../src/mic-button.ts";

function nestedBlock(source, parent, child) {
  const parentAt = source.indexOf(parent + " {");
  if (parentAt < 0) throw new Error("missing " + parent);
  const open = source.indexOf("{", parentAt);
  let depth = 0;
  let end = open;
  for (let i = open; i < source.length; i += 1) {
    if (source[i] === "{") depth += 1;
    else if (source[i] === "}") {
      depth -= 1;
      if (depth === 0) {
        end = i;
        break;
      }
    }
  }
  const body = source.slice(open + 1, end);
  const childAt = body.indexOf(child);
  if (childAt < 0) throw new Error(parent + " is missing " + child);
  const childOpen = body.indexOf("{", childAt);
  const childEnd = body.indexOf("}", childOpen);
  return body.slice(childOpen + 1, childEnd);
}

function decl(block, prop) {
  const match = block.match(new RegExp(prop + "\\s*:\\s*([^;]+)"));
  if (!match) throw new Error("missing " + prop + " in " + block);
  return match[1].trim().toLowerCase();
}

const idle = micStateClasses(false);
const live = micStateClasses(true);
if (idle.add !== MIC_OFF_CLASS || idle.remove !== MIC_ON_CLASS) {
  throw new Error("idle mic class was " + JSON.stringify(idle));
}
if (live.add !== MIC_ON_CLASS || live.remove !== MIC_OFF_CLASS) {
  throw new Error("listening mic class was " + JSON.stringify(live));
}
if (MIC_OFF_COLOR === MIC_ON_COLOR || MIC_OFF_COLOR === MIC_ON_FILL || MIC_ON_FILL === MIC_ON_HOVER_FILL) {
  throw new Error("on and off mic colors are not distinct");
}

const classes = new Set(["tool", MIC_OFF_CLASS]);
const button = {
  classList: {
    add(name) { classes.add(name); },
    remove(name) { classes.delete(name); },
    toggle(name, on) {
      if (on) classes.add(name);
      else classes.delete(name);
    },
    contains(name) { return classes.has(name); },
  },
};
paintMicButton(button, false);
if (!classes.has(MIC_OFF_CLASS) || classes.has(MIC_ON_CLASS) || classes.has("on")) {
  throw new Error("idle mic painted as listening: " + [...classes].join(" "));
}
paintMicButton(button, true);
if (!classes.has(MIC_ON_CLASS) || !classes.has("on") || classes.has(MIC_OFF_CLASS)) {
  throw new Error("listening mic painted as idle: " + [...classes].join(" "));
}
paintMicButton(button, false);
if (!classes.has(MIC_OFF_CLASS) || classes.has(MIC_ON_CLASS) || classes.has("on")) {
  throw new Error("mic stayed listening after it was turned off: " + [...classes].join(" "));
}

const scss = fs.readFileSync(new URL("../src/styles.scss", import.meta.url), "utf8");
const offRule = nestedBlock(scss, "#btnVoice", "&.mic-off");
const onRule = nestedBlock(scss, "#btnVoice", "&.mic-on {");
const hoverRule = nestedBlock(scss, "#btnVoice", "&.mic-on:hover");
const offColor = decl(offRule, "color");
const offFill = decl(offRule, "background");
const onColor = decl(onRule, "color");
const onFill = decl(onRule, "background");
const hoverFill = decl(hoverRule, "background");

if (offColor !== MIC_OFF_COLOR || offFill !== "transparent") {
  throw new Error("idle mic color is " + offColor + " on " + offFill);
}
if (onColor !== MIC_ON_COLOR || onFill !== MIC_ON_FILL) {
  throw new Error("listening mic color is " + onColor + " on " + onFill);
}
if (hoverFill !== MIC_ON_HOVER_FILL) {
  throw new Error("listening hover color is " + hoverFill);
}
if (offColor === onFill || offFill === onFill || onColor === offColor) {
  throw new Error("listening mic uses the idle color");
}

const html = fs.readFileSync(new URL("../public/index.html", import.meta.url), "utf8");
const micTag = html.slice(html.indexOf('id="btnVoice"') - 80, html.indexOf('id="btnVoice"') + 40);
if (!micTag.includes(MIC_OFF_CLASS) || micTag.includes(MIC_ON_CLASS)) {
  throw new Error("dictation button does not start idle: " + micTag);
}

const main = fs.readFileSync(new URL("../src/main.ts", import.meta.url), "utf8");
if (!main.includes("paintMicButton(mic, dictating)")) {
  throw new Error("dictation button does not paint listening versus idle");
}

console.log("ok");
