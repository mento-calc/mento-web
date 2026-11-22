// A rectangular section with its cage, as mento details it: the beam's drawing, and the continuous
// beam's thumbnail of each span. Shared, so both draw a section the same way.
import { dimBelow, dimLeft, fmt } from "./calculator.js";

// The section, drawn here rather than by beam.plot(), so the page never loads matplotlib: the cage
// as mento details it (py/beam.py reads beam.detailing_geometry), piece by piece. Geometry arrives
// in cm (or in) with the origin at the bottom left corner; SVG's y axis points down.
export function drawing(result, t, phone, U, size = null) {
  const { width, height, cover, stirrup, closed, ties, bars, labels, steel } = result.section;
  // `size` draws it in another frame: the continuous beam's thumbnails of each span
  const box = size ?? (phone ? { w: 343, h: 210, scale: 3 } : { w: 556, h: 300, scale: 3.5 });
  // The frame keeps a band on top for its label and its Copiá button (44 px tall on the phone),
  // the dimension lines below and on the left and the rebar labels on the right; a section too big
  // for what is left is drawn smaller (3.10).
  const note = `${width} × ${height}`;
  const room = { top: phone ? 56 : 40, bottom: 30, left: phone ? 40 : 60, right: phone ? 128 : 150 };
  const scale = Math.min(
    box.scale * U.scale,
    (box.h - room.top - room.bottom) / height,
    (box.w - room.left - room.right) / width,
  );
  const [w, h] = [width * scale, height * scale];
  // centred in what the dimension line and the labels leave
  const x0 = Math.round((room.left + box.w - room.right - w) / 2);
  const y0 = room.top + (box.h - room.top - room.bottom - h) / 2;
  const X = (x) => x0 + x * scale;
  const Y = (y) => y0 + (height - y) * scale;
  const px = (value) => value.toFixed(2);
  const parts = [`<rect class="dw-concrete" x="${x0}" y="${y0}" width="${w}" height="${h}"/>`];

  // The stirrups on the centrelines mento gives them, as thick as their bar and bent on its mandrel.
  const steelWidth = px(Math.max(1.6, stirrup.d * scale));
  const bend = Math.max(2, ((stirrup.bend + stirrup.d) / 2) * scale);
  for (const piece of closed) {
    parts.push(`<rect class="dw-stirrup" x="${px(X(piece.x0))}" y="${px(Y(piece.y1))}" width="${px((piece.x1 - piece.x0) * scale)}"`
      + ` height="${px((piece.y1 - piece.y0) * scale)}" rx="${px(bend)}" style="stroke-width:${steelWidth}"/>`);
  }
  // A leg between the outer two is a crosstie, as beam.plot() draws it: a 90° hook around the bar it
  // holds at the bottom, a 135° one at the top, both opening to the side that bar is on.
  const tail = Math.max(5, 6 * stirrup.d * scale);
  for (const tie of ties) {
    const [x, yb, yt, side, turn] = [X(tie.x), Y(tie.y0), Y(tie.y1), tie.side, tie.side > 0 ? 1 : 0];
    const r = Math.min(bend, (yb - yt) / 4);
    const over = [x + side * r * (1 + Math.SQRT1_2), yt + r * (1 - Math.SQRT1_2)];  // 135° round the top bar
    parts.push(`<path class="dw-stirrup" style="stroke-width:${steelWidth}" d="M${px(x + side * (r + tail))} ${px(yb)}H${px(x + side * r)}`
      + `A${px(r)} ${px(r)} 0 0 ${turn} ${px(x)} ${px(yb - r)}V${px(yt + r)}A${px(r)} ${px(r)} 0 0 ${turn} ${px(over[0])} ${px(over[1])}`
      + `l${px(side * tail * Math.SQRT1_2)} ${px(tail * Math.SQRT1_2)}"/>`);
  }
  if (!closed.length) {  // no stirrups, or none typed yet: where they would go, dashed
    const inset = cover * scale;
    parts.push(`<rect class="dw-stirrup dw-stirrup--none" x="${x0 + inset}" y="${y0 + inset}" width="${w - 2 * inset}" height="${h - 2 * inset}" rx="2"/>`);
  }
  // Every bar of the cage alike, as beam.plot() draws them: the ones the checks count, the mounting
  // bars that only hold the stirrups, and the skin of a deep beam. Their labels tell them apart.
  for (const bar of bars) {
    parts.push(`<circle class="dw-bar" data-kind="${bar.kind}" cx="${px(X(bar.x))}"`
      + ` cy="${px(Y(bar.y))}" r="${px(Math.max(1.6, (bar.d / 2) * scale))}"/>`);
  }
  // One label per row of bars, level with it, and the stirrups at mid-height.
  const words = { mounting: (text) => `${text} (${t.mounting})`, skin: (text) => `${text} ${t.per_side} (${t.skin})` };
  const texts = labels.filter((label) => label.kind !== "skin")
    .map((label) => ({ y: Y(label.y) + 4, text: words[label.kind]?.(label.bars) ?? label.bars, extra: "" }));
  if (result.rebar.st) texts.push({ y: y0 + h / 2 + 4, text: result.rebar.st, extra: " dw-label--stirrup" });
  for (const label of labels.filter((item) => item.kind === "skin")) texts.push({ y: Y(label.y) + 4, text: words.skin(label.bars), extra: "" });
  // Rows a few cm apart are closer on screen than a line of text: from the top down each label keeps
  // 13 px from the one above it, and from the bottom up they make room if that ran past the section.
  texts.sort((a, b) => a.y - b.y);
  texts.forEach((label, index) => { if (index) label.y = Math.max(label.y, texts[index - 1].y + 13); });
  for (let index = texts.length - 1, limit = y0 + h + 4; index >= 0; limit = texts[index].y - 13, index -= 1) {
    texts[index].y = Math.min(texts[index].y, limit);
  }
  const labelX = x0 + w + 12;
  for (const label of texts) {
    parts.push(`<text class="dw-label${label.extra}" x="${labelX}" y="${label.y.toFixed(1)}">${label.text}</text>`);
  }
  // the steel of the whole cage per volume of concrete, as beam.plot() writes it under the section
  if (steel) parts.push(`<text class="dw-dt" x="${labelX}" y="${y0 + h + 29}">${t.steel_ratio} ${steel}</text>`);
  parts.push(dimBelow(x0, x0 + w, y0 + h, y0 + h + 14, `${fmt(width)} ${U.len}`),
    dimLeft(y0, y0 + h, x0, x0 - 14, `${fmt(height)} ${U.len}`));
  const description = t.fig_section.replace("{section}", note)
    .replace("{bars}", [result.rebar.bot, result.rebar.top, result.rebar.st].filter(Boolean).join(", "));
  return `<svg viewBox="0 0 ${box.w} ${box.h}" width="${box.w}" height="${box.h}" style="max-width:100%;height:auto"`
    + ` role="img" aria-label="${description}">${parts.join("")}</svg>`;
}
