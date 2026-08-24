// Commons Family shared renderer + drivers.
//
// One canvas scene per module — the commons itself on top, the six cogs in a
// row underneath it, and a time series of the resource along the bottom:
//   cleanup       an orchard block drawn as apples over a river strip whose
//                 pollution gradient darkens as the water silts up
//   harvest       six patch cards with their apple counts, a tombstone glyph
//                 on every patch that was stripped bare
//   allelopathic  a stacked colour-share bar over the 60 plant slots with a
//                 ripeness overlay per colour
//   mushrooms     three mushroom rows plus reward particles that fly from the
//                 bite to every cog the bite pays — the only way a spectator
//                 can see who a blue mushroom paid
//
// The file is bullwhip's chrome with the board retargeted: same makeRenderer /
// buildScrub / renderFeed / updateScorebug / updateEndscreen / makeNameMap /
// attachReplay skeleton, same transport, same feed toggle. All state
// derivation happens server-side / wasm-side; this file only draws state
// objects:
//   {r, rounds, module, phase, done, reason,
//    seats:[{slot,alias,name,score,gain,extracted,public_effort,favorite,
//            frozen,patches,pending,say,disconnected}],
//    resource:{kind, ...}, series:{total[],maintenance[]},
//    flow:[{from,to,amount,kind}]}
(function () {
  "use strict";

  // Ink & Print palette, matching the coworld-ctf broadcast chrome.
  var COLORS = ["red", "blue", "green", "yellow", "violet", "orange"];
  var COLOR_HEX = {
    red: "#e0523a",
    blue: "#3f7cc4",
    green: "#45a85e",
    yellow: "#ddc531",
    violet: "#a86fd6",
    orange: "#e08a3a"
  };
  var BERRY_HEX = { red: "#e0523a", green: "#45a85e", blue: "#3f7cc4" };
  var PAPER = "#f2e8d8";
  var PAPER_DIM = "#b8ac98";
  var INK = "#2a1f16";
  var AMBER = "#e8a33d";
  var GHOST = "#8a7f72";
  var BARK = "#6b4c22";
  var LEAF = "#2f6b3a";
  var WATER = "#2d5c74";
  var STRIP = "rgba(242, 232, 216, 0.06)";
  var BERRY_ORDER = ["red", "green", "blue"];
  var MODULE_TITLE = {
    cleanup: "ORCHARD & RIVER",
    harvest: "SIX PATCHES",
    allelopathic: "SIXTY SLOTS",
    mushrooms: "THE MUSHROOM PATCH"
  };
  // Timing of the round transition: the resource redraws, reward particles fly.
  var ROUND_MS = 900;
  var FLOW_MS = 1200;
  var BUBBLE_HOLD_MS = 6000;

  function assetUrl(base, name) {
    return base.replace(/\/$/, "") + "/" + name;
  }

  function loadImages(base, names, done) {
    var images = {};
    var pending = names.length;
    names.forEach(function (name) {
      var img = new Image();
      img.onload = img.onerror = function () {
        pending -= 1;
        if (pending === 0) done(images);
      };
      img.src = assetUrl(base, name);
      images[name] = img;
    });
  }

  function seatColor(index) {
    return COLORS[index % COLORS.length];
  }

  function makeRenderer(canvas, assetBase, onReady) {
    var ctx = canvas.getContext("2d");
    var names = ["cog_red.png", "cog_blue.png", "cog_green.png",
      "cog_yellow.png", "cog_violet.png", "cog_orange.png",
      "apple.png", "mushroom_red.png", "mushroom_green.png",
      "mushroom_blue.png", "arena_floor.png"];
    loadImages(assetBase, names, function (images) {
      onReady({
        draw: function (view) { draw(ctx, canvas, images, view); }
      });
    });
  }

  function ellipsize(ctx, text, maxWidth) {
    if (ctx.measureText(text).width <= maxWidth) return text;
    var cut = text;
    while (cut.length > 1 && ctx.measureText(cut + "…").width > maxWidth) {
      cut = cut.slice(0, -1);
    }
    return cut + "…";
  }

  // Every string on the board goes through paint(). It ellipsizes to the frame
  // and then clamps the box so the glyphs land INSIDE the canvas on the first
  // draw and every draw after it — the softmax.com featured-match iframe is
  // about 360 px wide, and a caption anchored where there is no room is
  // invisible to the load signal, to the soak and to the screenshot.
  function paint(ctx, text, x, y, align, color) {
    var w = ctx.canvas.width;
    var h = ctx.canvas.height;
    if (w < 12 || h < 12) return;
    var t = ellipsize(ctx, String(text), Math.max(8, w - 8));
    var m = ctx.measureText(t);
    var asc = m.actualBoundingBoxAscent || 0;
    var desc = m.actualBoundingBoxDescent || 0;
    var left = align === "center" ? x - m.width / 2 :
      align === "right" ? x - m.width : x;
    left = Math.max(3, Math.min(left, w - m.width - 3));
    var top = Math.max(2, Math.min(y - asc, h - (asc + desc) - 2));
    ctx.textAlign = "left";
    ctx.textBaseline = "alphabetic";
    if (color) ctx.fillStyle = color;
    ctx.fillText(t, left, top + asc);
  }

  function hexToRgb(hex) {
    var n = parseInt(hex.slice(1), 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  function rgba(hex, alpha) {
    var c = hexToRgb(hex);
    return "rgba(" + c[0] + "," + c[1] + "," + c[2] + "," + alpha + ")";
  }

  function score(value) {
    return (Math.round((value || 0) * 10) / 10).toFixed(1);
  }

  function roundRect(ctx, x, y, w, h, r) {
    var radius = Math.max(0, Math.min(r, w / 2, h / 2));
    ctx.beginPath();
    ctx.moveTo(x + radius, y);
    ctx.arcTo(x + w, y, x + w, y + h, radius);
    ctx.arcTo(x + w, y + h, x, y + h, radius);
    ctx.arcTo(x, y + h, x, y, radius);
    ctx.arcTo(x, y, x + w, y, radius);
    ctx.closePath();
  }

  // ---- Layout --------------------------------------------------------------

  // Three stacked bands: the commons, the cog row, the chart. Everything is
  // measured from the frame, so the whole arena always fits whatever size the
  // viewer is embedded in — there is nothing to pan and nothing to zoom.
  function computeLayout(width, height) {
    var margin = 8;
    var chartH = Math.max(58, Math.min(height * 0.24, 150));
    var cogH = Math.max(52, Math.min(height * 0.24, 118));
    var boardTop = margin;
    var boardH = Math.max(40, height - chartH - cogH - margin * 2);
    var scale = Math.max(0.55, Math.min(1.25, width / 960));
    return {
      width: width, height: height, margin: margin, scale: scale,
      board: { x: margin, y: boardTop, w: width - margin * 2, h: boardH },
      cogs: { x: margin, y: boardTop + boardH, w: width - margin * 2, h: cogH },
      chart: { x: margin, y: height - chartH - margin, w: width - margin * 2,
        h: chartH }
    };
  }

  // ---- Drawing -------------------------------------------------------------

  function draw(ctx, canvas, images, view) {
    var w = canvas.width;
    var h = canvas.height;
    if (!w || !h) return;
    var L = computeLayout(w, h);
    var resource = view.resource || {};
    var fx = view.effects || {};
    var now = view.now || Date.now();

    var floor = images["arena_floor.png"];
    if (floor && floor.width) {
      ctx.fillStyle = ctx.createPattern(floor, "repeat");
    } else {
      ctx.fillStyle = "#16110d";
    }
    ctx.fillRect(0, 0, w, h);
    ctx.fillStyle = "rgba(18, 13, 9, 0.5)";
    ctx.fillRect(0, 0, w, h);

    ctx.save();
    ctx.fillStyle = STRIP;
    roundRect(ctx, L.board.x, L.board.y, L.board.w, L.board.h, 10 * L.scale);
    ctx.fill();
    ctx.restore();

    ctx.font = "700 " + Math.round(11 * L.scale) +
      "px 'rajdhani', system-ui, sans-serif";
    paint(ctx, MODULE_TITLE[view.module] || "THE COMMONS",
      L.board.x + 8, L.board.y + 4, "left", PAPER_DIM);

    switch (view.module) {
      case "harvest": drawPatches(ctx, images, L, resource); break;
      case "allelopathic": drawField(ctx, L, resource); break;
      case "mushrooms": drawMushrooms(ctx, images, L, resource); break;
      default: drawOrchard(ctx, images, L, resource); break;
    }

    drawCogRow(ctx, images, L, view, now, fx);
    if (view.module === "mushrooms") {
      drawFlow(ctx, L, view, now, fx);
    }
    drawChart(ctx, L.chart, view, L.scale);
  }

  // -- cleanup ---------------------------------------------------------------

  function drawOrchard(ctx, images, L, resource) {
    var box = L.board;
    var riverH = Math.max(18, box.h * 0.26);
    var orchard = { x: box.x + 6, y: box.y + 18 * L.scale,
      w: box.w - 12, h: box.h - riverH - 24 * L.scale };
    var apples = Math.max(0, resource.apples || 0);
    var capacity = resource.capacity || 100;
    var dead = !!resource.dead;

    // Grass bed, sized by how much of the orchard is still standing.
    ctx.save();
    ctx.fillStyle = dead ? "rgba(70, 58, 44, 0.55)" : "rgba(47, 107, 58, 0.35)";
    roundRect(ctx, orchard.x, orchard.y, orchard.w, Math.max(6, orchard.h),
      6 * L.scale);
    ctx.fill();
    ctx.restore();

    var shown = Math.min(60, Math.ceil(apples));
    var cols = Math.max(6, Math.min(20, Math.floor(orchard.w /
      (18 * L.scale))));
    var rows = Math.max(1, Math.ceil(shown / cols));
    var cell = Math.min(orchard.w / cols, Math.max(10, orchard.h / (rows + 0.6)));
    var sprite = images["apple.png"];
    for (var i = 0; i < shown; i++) {
      var cx = orchard.x + (i % cols) * cell + cell / 2;
      var cy = orchard.y + Math.floor(i / cols) * cell + cell * 0.6;
      if (sprite && sprite.width) {
        ctx.globalAlpha = dead ? 0.45 : 1;
        ctx.drawImage(sprite, cx - cell * 0.4, cy - cell * 0.4, cell * 0.8,
          cell * 0.8);
        ctx.globalAlpha = 1;
      } else {
        ctx.fillStyle = dead ? GHOST : COLOR_HEX.red;
        ctx.beginPath();
        ctx.arc(cx, cy, cell * 0.3, 0, Math.PI * 2);
        ctx.fill();
      }
    }

    // The river: a strip whose gradient darkens with pollution.
    var pollution = Math.max(0, Math.min(1, resource.pollution || 0));
    var river = { x: box.x + 6, y: box.y + box.h - riverH - 2,
      w: box.w - 12, h: riverH - 4 };
    var gradient = ctx.createLinearGradient(river.x, river.y, river.x,
      river.y + river.h);
    gradient.addColorStop(0, rgba(WATER, 0.85 - 0.5 * pollution));
    gradient.addColorStop(1, "rgba(" + Math.round(40 + 30 * pollution) + "," +
      Math.round(50 - 30 * pollution) + "," + Math.round(40 - 20 * pollution) +
      ",0.95)");
    ctx.fillStyle = gradient;
    roundRect(ctx, river.x, river.y, river.w, river.h, 4 * L.scale);
    ctx.fill();
    // Silt flecks: more of them the dirtier it is.
    ctx.fillStyle = "rgba(90, 70, 40, " + (0.15 + 0.55 * pollution) + ")";
    var flecks = Math.round(6 + pollution * 40);
    for (var f = 0; f < flecks; f++) {
      var fxp = river.x + ((f * 97) % Math.max(1, Math.round(river.w)));
      var fyp = river.y + ((f * 53) % Math.max(1, Math.round(river.h)));
      ctx.fillRect(fxp, fyp, 3 * L.scale, 2 * L.scale);
    }

    ctx.font = "700 " + Math.round(15 * L.scale) +
      "px 'rajdhani', system-ui, sans-serif";
    paint(ctx, (Math.round(apples * 10) / 10) + " / " + capacity + " apples",
      box.x + box.w - 8, box.y + 4, "right", dead ? COLOR_HEX.red : PAPER);
    ctx.font = "700 " + Math.round(12 * L.scale) +
      "px 'rajdhani', system-ui, sans-serif";
    paint(ctx, dead ? "ORCHARD DEAD — NOTHING REGROWS" :
      "river " + Math.round(pollution * 100) + "% silted",
      river.x + 6, river.y + river.h - 15 * L.scale, "left",
      dead ? COLOR_HEX.red : PAPER_DIM);
  }

  // -- harvest ---------------------------------------------------------------

  function drawPatches(ctx, images, L, resource) {
    var box = L.board;
    var patches = resource.patches || [];
    if (!patches.length) return;
    var cols = patches.length <= 3 ? patches.length : 3;
    var rows = Math.ceil(patches.length / cols);
    var pad = 6 * L.scale;
    var top = box.y + 18 * L.scale;
    var cw = (box.w - pad * (cols + 1)) / cols;
    var ch = (box.h - 20 * L.scale - pad * (rows + 1)) / rows;
    var sprite = images["apple.png"];

    patches.forEach(function (patch, index) {
      var cx = box.x + pad + (index % cols) * (cw + pad);
      var cy = top + Math.floor(index / cols) * (ch + pad);
      ctx.save();
      ctx.fillStyle = patch.dead ? "rgba(60, 50, 40, 0.55)" :
        "rgba(47, 107, 58, 0.30)";
      roundRect(ctx, cx, cy, cw, ch, 6 * L.scale);
      ctx.fill();
      ctx.strokeStyle = patch.dead ? "rgba(224, 82, 58, 0.55)" :
        "rgba(242, 232, 216, 0.16)";
      ctx.lineWidth = 1.5;
      ctx.stroke();
      ctx.restore();

      if (patch.dead) {
        // Tombstone: a slab with a cross, and nothing growing on it.
        var tw = Math.min(cw * 0.34, ch * 0.5);
        var tx = cx + cw / 2 - tw / 2;
        var ty = cy + ch * 0.3;
        ctx.fillStyle = "#6c6259";
        roundRect(ctx, tx, ty, tw, tw * 1.2, tw * 0.45);
        ctx.fill();
        ctx.strokeStyle = "#3a342e";
        ctx.lineWidth = Math.max(2, tw * 0.12);
        ctx.beginPath();
        ctx.moveTo(tx + tw / 2, ty + tw * 0.28);
        ctx.lineTo(tx + tw / 2, ty + tw * 0.92);
        ctx.moveTo(tx + tw * 0.24, ty + tw * 0.5);
        ctx.lineTo(tx + tw * 0.76, ty + tw * 0.5);
        ctx.stroke();
      } else {
        var shown = Math.min(20, Math.ceil(patch.stock || 0));
        var per = Math.max(3, Math.min(5, Math.floor(cw / (14 * L.scale))));
        var cell = Math.min(cw / per, ch / 3.4);
        for (var i = 0; i < shown; i++) {
          var ax = cx + 4 + (i % per) * cell + cell / 2;
          var ay = cy + ch * 0.34 + Math.floor(i / per) * cell * 0.9;
          if (sprite && sprite.width) {
            ctx.drawImage(sprite, ax - cell * 0.34, ay - cell * 0.34,
              cell * 0.68, cell * 0.68);
          } else {
            ctx.fillStyle = COLOR_HEX.red;
            ctx.beginPath();
            ctx.arc(ax, ay, cell * 0.25, 0, Math.PI * 2);
            ctx.fill();
          }
        }
      }

      ctx.font = "700 " + Math.round(12 * L.scale) +
        "px 'rajdhani', system-ui, sans-serif";
      paint(ctx, "PATCH " + patch.id, cx + 6, cy + 4, "left", PAPER_DIM);
      ctx.font = "700 " + Math.round(15 * L.scale) +
        "px 'rajdhani', system-ui, sans-serif";
      paint(ctx, patch.dead ? "stripped bare" :
        (Math.round((patch.stock || 0) * 10) / 10) + " apples",
        cx + cw - 6, cy + 4, "right", patch.dead ? COLOR_HEX.red : PAPER);
      var holders = (patch.holders || []).join(" + ");
      if (holders) {
        ctx.font = "600 " + Math.round(10 * L.scale) +
          "px 'rajdhani', system-ui, sans-serif";
        paint(ctx, holders, cx + 6, cy + ch - 14 * L.scale, "left", AMBER);
      }
    });
  }

  // -- allelopathic ----------------------------------------------------------

  function drawField(ctx, L, resource) {
    var box = L.board;
    var planted = resource.planted || {};
    var ripe = resource.ripe || {};
    var size = resource.field_size || 60;
    var barY = box.y + box.h * 0.34;
    var barH = Math.max(26, box.h * 0.30);
    var barX = box.x + 8;
    var barW = box.w - 16;

    ctx.save();
    ctx.fillStyle = "rgba(18, 13, 9, 0.5)";
    roundRect(ctx, barX, barY, barW, barH, 5 * L.scale);
    ctx.fill();
    ctx.restore();

    var x = barX;
    BERRY_ORDER.forEach(function (color) {
      var slots = planted[color] || 0;
      var width = barW * slots / Math.max(1, size);
      if (width <= 0) return;
      ctx.fillStyle = rgba(BERRY_HEX[color], 0.42);
      ctx.fillRect(x, barY, width, barH);
      // Ripeness overlay: the solid part of the band is the ripe fraction.
      var ripeShare = slots ? Math.max(0, Math.min(1, (ripe[color] || 0) / slots)) : 0;
      ctx.fillStyle = BERRY_HEX[color];
      ctx.fillRect(x, barY + barH * (1 - ripeShare), width, barH * ripeShare);
      ctx.strokeStyle = "rgba(18, 13, 9, 0.7)";
      ctx.lineWidth = 1;
      ctx.strokeRect(x + 0.5, barY + 0.5, Math.max(1, width - 1), barH - 1);
      ctx.font = "700 " + Math.round(12 * L.scale) +
        "px 'rajdhani', system-ui, sans-serif";
      if (width > 40 * L.scale) {
        paint(ctx, slots + " slots", x + width / 2, barY + barH + 4 * L.scale,
          "center", PAPER);
        paint(ctx, (Math.round((ripe[color] || 0) * 10) / 10) + " ripe",
          x + width / 2, barY + barH + 18 * L.scale, "center", PAPER_DIM);
      }
      x += width;
    });

    // Berry cluster above the bar, one dot per ripe berry, capped.
    var dotY = barY - 16 * L.scale;
    var dotX = barX;
    BERRY_ORDER.forEach(function (color) {
      var count = Math.min(24, Math.ceil(ripe[color] || 0));
      for (var i = 0; i < count; i++) {
        ctx.fillStyle = BERRY_HEX[color];
        ctx.beginPath();
        ctx.arc(dotX + 5 * L.scale, dotY, 3.2 * L.scale, 0, Math.PI * 2);
        ctx.fill();
        dotX += 9 * L.scale;
        if (dotX > barX + barW - 10 * L.scale) { dotX = barX; dotY -= 9 * L.scale; }
      }
      dotX += 12 * L.scale;
    });

    ctx.font = "700 " + Math.round(13 * L.scale) +
      "px 'rajdhani', system-ui, sans-serif";
    var total = BERRY_ORDER.reduce(function (sum, color) {
      return sum + (ripe[color] || 0);
    }, 0);
    paint(ctx, (Math.round(total * 10) / 10) + " ripe berries in the field",
      box.x + box.w - 8, box.y + 4, "right", total > 0 ? PAPER : COLOR_HEX.red);
  }

  // -- mushrooms -------------------------------------------------------------

  function mushroomRowY(L, index) {
    return L.board.y + 26 * L.scale + index * ((L.board.h - 30 * L.scale) / 3);
  }

  function drawMushrooms(ctx, images, L, resource) {
    var box = L.board;
    var counts = resource.counts || {};
    var payoffs = { red: "+1.0 to the eater", green: "+2.0 shared by all",
      blue: "+3.0 shared by everyone else" };
    BERRY_ORDER.forEach(function (color, index) {
      var y = mushroomRowY(L, index);
      var rowH = (box.h - 30 * L.scale) / 3;
      ctx.save();
      ctx.fillStyle = rgba(BERRY_HEX[color], 0.10);
      roundRect(ctx, box.x + 6, y, box.w - 12, rowH - 4 * L.scale, 5 * L.scale);
      ctx.fill();
      ctx.restore();

      var count = Math.max(0, Math.round(counts[color] || 0));
      var sprite = images["mushroom_" + color + ".png"];
      var cell = Math.min(rowH * 0.7, (box.w - 150 * L.scale) /
        Math.max(6, count || 6));
      for (var i = 0; i < Math.min(15, count); i++) {
        var mx = box.x + 14 * L.scale + i * (cell + 2 * L.scale);
        var my = y + rowH * 0.5;
        if (sprite && sprite.width) {
          ctx.drawImage(sprite, mx, my - cell * 0.5, cell, cell);
        } else {
          ctx.fillStyle = BERRY_HEX[color];
          ctx.beginPath();
          ctx.arc(mx + cell / 2, my, cell * 0.35, 0, Math.PI * 2);
          ctx.fill();
        }
      }
      ctx.font = "700 " + Math.round(12 * L.scale) +
        "px 'rajdhani', system-ui, sans-serif";
      paint(ctx, count + " " + color + " · " + payoffs[color],
        box.x + box.w - 8, y + 3 * L.scale, "right", BERRY_HEX[color]);
    });
  }

  function drawFlow(ctx, L, view, now, fx) {
    var flow = view.flow || [];
    if (!flow.length) return;
    var seats = view.seats || [];
    var age = typeof fx.resolveAt === "number" ? now - fx.resolveAt : FLOW_MS;
    var t = Math.max(0.05, Math.min(1, age / FLOW_MS));
    flow.forEach(function (entry, index) {
      var from = cogCentre(L, entry.from, seats.length);
      var to = cogCentre(L, entry.to, seats.length);
      var rowIndex = BERRY_ORDER.indexOf(entry.kind);
      var startY = rowIndex >= 0 ? mushroomRowY(L, rowIndex) +
        L.board.h * 0.12 : L.board.y + L.board.h / 2;
      var sx = from.x;
      var px = sx + (to.x - sx) * t;
      var py = startY + (to.y - startY) * t;
      ctx.save();
      ctx.globalAlpha = 0.35 + 0.65 * (1 - Math.abs(0.5 - t) * 2) + 0.2;
      ctx.fillStyle = BERRY_HEX[entry.kind] || AMBER;
      ctx.beginPath();
      ctx.arc(px, py, Math.max(2.5, 4 * L.scale), 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();
      void index;
    });
  }

  // -- the cog row -----------------------------------------------------------

  function cogCentre(L, slot, count) {
    var n = Math.max(1, count || 6);
    var pitch = L.cogs.w / n;
    return { x: L.cogs.x + pitch * (slot + 0.5), y: L.cogs.y + L.cogs.h * 0.46 };
  }

  function drawCogRow(ctx, images, L, view, now, fx) {
    var seats = view.seats || [];
    if (!seats.length) return;
    var pitch = L.cogs.w / seats.length;
    var size = Math.max(22, Math.min(L.cogs.h * 0.52, pitch * 0.5));
    seats.forEach(function (seat, index) {
      var centre = cogCentre(L, index, seats.length);
      var color = seatColor(index);
      var sprite = images["cog_" + color + ".png"];

      if (seat.pending && !view.done) {
        ctx.save();
        ctx.strokeStyle = AMBER;
        ctx.lineWidth = 2;
        ctx.setLineDash([5, 4]);
        ctx.beginPath();
        ctx.arc(centre.x, centre.y, size * 0.62, 0, Math.PI * 2);
        ctx.stroke();
        ctx.restore();
      }
      ctx.save();
      ctx.globalAlpha = seat.disconnected ? 0.35 : 1;
      if (sprite && sprite.width) {
        ctx.drawImage(sprite, centre.x - size / 2, centre.y - size / 2, size, size);
      } else {
        ctx.fillStyle = COLOR_HEX[color];
        ctx.fillRect(centre.x - size / 3, centre.y - size / 3, size / 1.5,
          size / 1.5);
      }
      ctx.restore();

      ctx.font = "700 " + Math.round(11 * L.scale) +
        "px 'rajdhani', system-ui, sans-serif";
      paint(ctx, seat.alias || ("Cog " + index), centre.x,
        centre.y - size * 0.62 - 11 * L.scale, "center", COLOR_HEX[color]);
      ctx.font = "700 " + Math.round(13 * L.scale) +
        "px 'rajdhani', system-ui, sans-serif";
      paint(ctx, score(seat.score), centre.x, centre.y + size * 0.55, "center",
        PAPER);
      if (seat.gain) {
        ctx.font = "700 " + Math.round(11 * L.scale) +
          "px 'rajdhani', system-ui, sans-serif";
        paint(ctx, "+" + score(seat.gain), centre.x,
          centre.y + size * 0.55 + 13 * L.scale, "center", AMBER);
      }
      if (seat.frozen) {
        ctx.font = "700 " + Math.round(10 * L.scale) +
          "px 'rajdhani', system-ui, sans-serif";
        paint(ctx, "DIGESTING", centre.x, centre.y + size * 0.55 +
          13 * L.scale, "center", PAPER_DIM);
      }
      if (seat.say) {
        var sayAge = fx.sayAt && typeof fx.sayAt[index] === "number" ?
          now - fx.sayAt[index] : BUBBLE_HOLD_MS;
        var alpha = sayAge < BUBBLE_HOLD_MS ? 1 :
          Math.max(0.4, 1 - (sayAge - BUBBLE_HOLD_MS) / 4000);
        drawBubble(ctx, centre.x, centre.y - size * 0.62 - 14 * L.scale,
          seat.say, pitch * 1.5, L.scale, alpha);
      }
    });
    void ROUND_MS;
  }

  function wrapLines(ctx, text, maxWidth, maxLines) {
    var words = String(text).split(/\s+/);
    var lines = [];
    var line = "";
    words.forEach(function (word) {
      var probe = line ? line + " " + word : word;
      if (ctx.measureText(probe).width > maxWidth && line) {
        lines.push(line);
        line = word;
      } else {
        line = probe;
      }
    });
    if (line) lines.push(line);
    var overflow = lines.length > maxLines;
    lines = lines.slice(0, maxLines);
    if (overflow && lines.length) {
      lines[lines.length - 1] = ellipsize(ctx, lines[lines.length - 1] + "…",
        maxWidth);
    }
    return lines.map(function (l) { return ellipsize(ctx, l, maxWidth); });
  }

  // The bubble reserves its own band: it is measured first and its box is
  // clamped into the frame, so a cog near the top edge pushes its line DOWN
  // instead of drawing it at a negative y where nothing can see it.
  function drawBubble(ctx, x, bottom, text, maxW, scale, alpha) {
    ctx.save();
    ctx.globalAlpha = alpha;
    ctx.font = Math.round(10.5 * scale) +
      "px -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif";
    var pad = 5 * scale;
    var lineH = 12 * scale;
    var width = Math.max(40, Math.min(maxW, ctx.canvas.width - 12));
    var lines = wrapLines(ctx, text, width - pad * 2, 2);
    var bw = 0;
    lines.forEach(function (l) { bw = Math.max(bw, ctx.measureText(l).width); });
    bw = Math.min(width, bw + pad * 2);
    var bh = lines.length * lineH + pad * 2 - 2;
    var bx = Math.max(3, Math.min(x - bw / 2, ctx.canvas.width - bw - 3));
    var by = Math.max(3, Math.min(bottom - bh - 5 * scale,
      ctx.canvas.height - bh - 3));
    ctx.shadowColor = "rgba(0,0,0,0.6)";
    ctx.shadowBlur = 5;
    ctx.fillStyle = PAPER;
    roundRect(ctx, bx, by, bw, bh, 4 * scale);
    ctx.fill();
    ctx.shadowColor = "transparent";
    ctx.fillStyle = INK;
    lines.forEach(function (l, i) {
      paint(ctx, l, bx + pad, by + pad + i * lineH, "left", INK);
    });
    ctx.restore();
  }

  // ---- Chart ---------------------------------------------------------------

  // The module's primary resource over rounds, with the maintenance quantity
  // as a second trace: pollution, dead patches, plurality share, or the
  // green+blue share of the mushroom patch.
  function drawChart(ctx, rect, view, scale) {
    var series = view.series || {};
    var total = series.total || [];
    var maintenance = series.maintenance || [];
    var rounds = Math.max(view.rounds || 0, 4);
    var padL = 28 * scale;
    var padR = 30 * scale;
    var padT = 14 * scale;
    var padB = 12 * scale;
    var x0 = rect.x + padL;
    var x1 = rect.x + rect.w - padR;
    var y0 = rect.y + padT;
    var y1 = rect.y + rect.h - padB;
    if (x1 <= x0 || y1 <= y0) return;

    var maxY = 1;
    total.forEach(function (v) { if (v > maxY) maxY = v; });
    maxY = Math.ceil(maxY * 1.15);
    var maxM = 1;
    maintenance.forEach(function (v) { if (v > maxM) maxM = v; });

    function px(r) { return x0 + (x1 - x0) * r / rounds; }
    function py(v) { return y1 - (y1 - y0) * v / maxY; }
    function pm(v) { return y1 - (y1 - y0) * v / maxM; }

    ctx.save();
    ctx.fillStyle = "rgba(18, 13, 9, 0.55)";
    roundRect(ctx, rect.x, rect.y, rect.w, rect.h, 5 * scale);
    ctx.fill();
    ctx.strokeStyle = "rgba(242, 232, 216, 0.12)";
    ctx.lineWidth = 1;
    ctx.stroke();

    ctx.font = "700 " + Math.round(9.5 * scale) +
      "px 'rajdhani', system-ui, sans-serif";
    paint(ctx, chartTitle(view.module), rect.x + 7 * scale, rect.y + 2 * scale,
      "left", PAPER_DIM);

    ctx.strokeStyle = "rgba(242, 232, 216, 0.12)";
    for (var g = 0; g <= 2; g++) {
      var gy = py(maxY * g / 2);
      ctx.beginPath();
      ctx.moveTo(x0, gy);
      ctx.lineTo(x1, gy);
      ctx.stroke();
      ctx.font = "600 " + Math.round(8.5 * scale) +
        "px 'rajdhani', system-ui, sans-serif";
      paint(ctx, String(Math.round(maxY * g / 2)), x0 - 3 * scale,
        gy - 4 * scale, "right", GHOST);
    }

    if (maintenance.length) {
      ctx.strokeStyle = rgba(AMBER, 0.75);
      ctx.lineWidth = 1.5;
      ctx.setLineDash([4, 3]);
      ctx.beginPath();
      maintenance.forEach(function (v, i) {
        var x = px(i + 1);
        if (i === 0) ctx.moveTo(x, pm(v)); else ctx.lineTo(x, pm(v));
      });
      ctx.stroke();
      ctx.setLineDash([]);
    }
    if (total.length) {
      ctx.strokeStyle = COLOR_HEX.green;
      ctx.lineWidth = 2;
      ctx.lineJoin = "round";
      ctx.beginPath();
      total.forEach(function (v, i) {
        var x = px(i + 1);
        if (i === 0) ctx.moveTo(x, py(v)); else ctx.lineTo(x, py(v));
      });
      ctx.stroke();
      var last = total.length - 1;
      ctx.fillStyle = COLOR_HEX.green;
      ctx.beginPath();
      ctx.arc(px(last + 1), py(total[last]), 2.6 * scale, 0, Math.PI * 2);
      ctx.fill();
    }

    var nowX = px(Math.max(0, view.r || 0));
    ctx.strokeStyle = rgba(AMBER, 0.7);
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    ctx.moveTo(nowX, y0 - 3 * scale);
    ctx.lineTo(nowX, y1);
    ctx.stroke();
    ctx.restore();
  }

  function chartTitle(module) {
    switch (module) {
      case "harvest": return "APPLES IN THE PATCHES · DEAD PATCHES";
      case "allelopathic": return "RIPE BERRIES · BIGGEST COLOUR SHARE";
      case "mushrooms": return "MUSHROOMS STANDING · SHARE THAT PAYS OTHERS";
      default: return "APPLES · POLLUTION";
    }
  }

  // ---- Names ---------------------------------------------------------------

  // The cogs only ever hear anonymous aliases ("Cog-A"); the payload carries
  // the real policy names separately, spectator-side only. A name map swaps
  // them in wherever a name is RENDERED while the underlying events keep the
  // aliases. Baseline fillers keep their alias.
  function isBaselineFiller(name) {
    return /^baseline(\s*\(\d+\))?$/i.test(name);
  }

  function makeNameMap(tableNames, policyNames) {
    var table = tableNames || [];
    var display = table.map(function (name, i) {
      var policy = policyNames && policyNames[i];
      return (policy && !isBaselineFiller(policy)) ? policy : name;
    });
    var byAlias = {};
    table.forEach(function (name, i) {
      if (name && display[i] && display[i] !== name) byAlias[name] = display[i];
    });
    var aliases = Object.keys(byAlias);
    var pattern = aliases.length ? new RegExp(
      "\\b(?:" + aliases.map(function (name) {
        return name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      }).join("|") + ")\\b", "g") : null;
    return {
      alias: function (i) { return table[i] || ("Cog " + i); },
      seat: function (i) { return display[i] || ("Cog " + i); },
      text: function (text) {
        if (!pattern) return text;
        return String(text).replace(pattern, function (match) {
          return byAlias[match];
        });
      }
    };
  }

  function applyNames(seats, nameMap) {
    return (seats || []).map(function (seat, i) {
      var copy = Object.assign({}, seat);
      copy.name = nameMap.seat(i);
      return copy;
    });
  }

  function clampName(name) {
    var n = String(name || "");
    return n.length > 26 ? n.slice(0, 25) + "…" : n;
  }

  // ---- Event feed ----------------------------------------------------------

  // Every event carries spectator English written by the engine, so the rules
  // are described in exactly one place. The feed only decides what colour it is.
  function describeEvent(event, nameMap) {
    var text = event.text || "";
    if (!text) return JSON.stringify(event);
    return nameMap ? nameMap.text(text) : text;
  }

  var FEED_CLASS = {
    chat: "feed-say",
    sanction: "feed-death",
    collapse: "feed-death",
    patch_dead: "feed-death",
    fallback: "feed-notes",
    no_submission: "feed-notes",
    digesting: "feed-notes",
    void: "feed-notes",
    trespass: "feed-notes",
    unheld: "feed-notes",
    barren: "feed-death",
    deadline: "feed-end",
    round_end: "feed-week",
    resolve: "feed-shot",
    episode_end: "feed-end"
  };

  function blockHead(block) {
    return block < 0 ? "SETUP" : "ROUND " + (block + 1);
  }

  function renderFeed(element, events, nameMap, currentIndex) {
    if (!element) return;
    var live = currentIndex === undefined;
    var limit = live ? events.length : currentIndex;
    var html = "";
    var lastBlock = null;
    for (var i = 0; i < events.length; i++) {
      var event = events[i];
      var block = event.kind === "episode_start" ? -1 : (event.r || 0);
      if (block !== lastBlock) {
        html += '<div class="feed-round-head">' + blockHead(block) + "</div>";
        lastBlock = block;
      }
      var cls = "feed-line " + (FEED_CLASS[event.kind] || "feed-it") +
        (typeof event.slot === "number" ?
          " seat" + (event.slot % COLORS.length) : "") +
        (i >= limit ? " feed-future" : "");
      html += '<div class="' + cls + '">' +
        escapeHtml(describeEvent(event, nameMap)) + "</div>";
    }
    element.innerHTML = html;

    if (live || limit >= events.length) {
      element.scrollTop = element.scrollHeight;
      return;
    }
    var lines = element.querySelectorAll(".feed-line");
    var target = null;
    for (var l = 0; l < lines.length; l++) {
      if (!lines[l].classList.contains("feed-future")) target = lines[l];
    }
    if (target && element.dataset.anchor !== String(limit)) {
      element.dataset.anchor = String(limit);
      element.scrollTo({
        top: Math.max(target.offsetTop - element.offsetTop -
          element.clientHeight * 0.6, 0)
      });
    }
  }

  function escapeHtml(text) {
    return String(text).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  // ---- Animation bookkeeping ----------------------------------------------

  function makeEffects() {
    var seen = 0;
    var roundAt = null;
    var resolveAt = null;
    var sayAt = [];
    return {
      // `quiet` (a scrub jump): the whole prefix lands at once, so only the
      // newest event gets to animate.
      absorb: function (events, quiet) {
        var now = Date.now();
        for (; seen < events.length; seen++) {
          var event = events[seen];
          var animate = !quiet || seen >= events.length - 1;
          if (event.kind === "round_open") {
            roundAt = animate ? now : null;
            sayAt = [];
          } else if (event.kind === "resolve") {
            resolveAt = animate ? now : null;
          } else if (event.kind === "chat" && typeof event.slot === "number") {
            sayAt[event.slot] = animate ? now : null;
          }
        }
      },
      reset: function () {
        seen = 0; roundAt = null; resolveAt = null; sayAt = [];
      },
      view: function () {
        return { effects: { roundAt: roundAt, resolveAt: resolveAt,
          sayAt: sayAt.slice() } };
      }
    };
  }

  // ---- Scorebug, header, endscreen ----------------------------------------

  function matchHeader(state) {
    var parts = [];
    if (state) {
      var total = state.rounds || 0;
      parts.push("ROUND " + ((state.r || 0) + 1) + (total ? " OF " + total : ""));
      if (state.done) {
        parts.push("FINAL");
      } else if (state.seats) {
        var waiting = state.seats.filter(function (s) { return s.pending; });
        parts.push(waiting.length ? "WAITING ON " + waiting.length : "SETTLED");
      }
    }
    return parts.join(" · ");
  }

  function maintenanceChip(module, seat) {
    var units = Math.round(seat.public_effort || 0);
    switch (module) {
      case "cleanup": return "clean ×" + units;
      case "harvest": return "held " + units;
      case "allelopathic": return "planted ×" + units;
      case "mushrooms": return "shared ×" + units;
      default: return "";
    }
  }

  function moduleBadge(module, seat) {
    if (module === "allelopathic" && seat.favorite) {
      return '<span class="plate-fav" style="background:' +
        (BERRY_HEX[seat.favorite] || GHOST) + '" title="secret favourite: ' +
        escapeHtml(seat.favorite) + '"></span>';
    }
    if (module === "mushrooms" && seat.frozen) {
      return '<span class="plate-badge">FROZEN</span>';
    }
    if (module === "harvest" && seat.patches && seat.patches.length) {
      return '<span class="plate-badge">P' + seat.patches.join("/") + "</span>";
    }
    return "";
  }

  function updateScorebug(container, state, nameMap) {
    if (!container || !state || !state.seats) return;
    var html = "";
    state.seats.forEach(function (seat, index) {
      var alias = nameMap ? nameMap.alias(index) : seat.alias;
      var display = nameMap ? nameMap.seat(index) : seat.name;
      var tip = alias + " · took " + score(seat.extracted) + " · upkeep " +
        Math.round(seat.public_effort || 0) +
        (display && display !== alias ? " · " + display : "");
      html += '<div class="plate ' + seatColor(index) +
        (seat.disconnected ? " dead" : "") + '" title="' +
        escapeHtml(tip) + '">' +
        '<span class="plate-cog" style="background-image:url(./assets/cog_' +
        seatColor(index) + '.png)"></span>' +
        '<span class="plate-name">' + escapeHtml(clampName(alias)) + "</span>" +
        (seat.pending && !state.done ? '<span class="plate-it">▶</span>' : "") +
        '<span class="plate-score">' + escapeHtml(score(seat.score)) + "</span>" +
        (display && display !== alias ?
          '<span class="plate-label">' + escapeHtml(clampName(display)) +
          "</span>" : "") +
        '<span class="plate-backlog">' +
        escapeHtml(maintenanceChip(state.module, seat)) + "</span>" +
        moduleBadge(state.module, seat) +
        "</div>";
    });
    if (container.dataset.html !== html) {
      container.dataset.html = html;
      container.innerHTML = html;
    }
  }

  function reasonLine(results) {
    switch (results.reason) {
      case "deadline":
        return "episode deadline: scored on " + (results.rounds || 0) +
          " rounds";
      case "no_players":
        return "no cog ever connected";
      default: return "";
    }
  }

  // Final standings overlay: verdict up top, ranked rows below.
  function updateEndscreen(container, results, show, nameMap, module) {
    if (!container) return;
    container.classList.toggle("show", !!show);
    if (!show || !results || container.dataset.built === "yes") return;
    container.dataset.built = "yes";
    var aliases = results.aliases || [];
    var names = (results.names || []).map(function (name, i) {
      return nameMap ? nameMap.seat(i) : name;
    });
    var scores = results.scores || [];
    var order = scores.map(function (_, i) { return i; });
    order.sort(function (a, b) { return (scores[b] || 0) - (scores[a] || 0); });
    var topIndex = order.length ? order[0] : -1;
    var level = order.every(function (i) {
      return (scores[i] || 0) === (scores[topIndex] || 0);
    });
    var verdictColor = !level && topIndex >= 0 ? seatColor(topIndex) : "";
    var verdict = !level && topIndex >= 0 ?
      escapeHtml(names[topIndex] || aliases[topIndex] || "") + " TOOK THE MOST" :
      "ALL LEVEL";
    var survived = results.collapse_round === null &&
      !(results.dead_patches || []).length;
    var reason = reasonLine(results);
    var html = '<div class="end-panel">' +
      '<div class="end-title">FINAL — ' + (results.rounds || 0) + " ROUND" +
      ((results.rounds || 0) === 1 ? "" : "S") + " · WELFARE " +
      escapeHtml(score(results.welfare)) + " · " +
      (survived ? "THE COMMONS SURVIVED" : "THE COMMONS DID NOT SURVIVE") +
      "</div>" +
      '<div class="end-verdict ' + verdictColor + '">' + verdict + "</div>" +
      (reason ? '<div class="end-reason">' + escapeHtml(reason) + "</div>" : "") +
      '<div class="end-rows">' +
      '<span class="end-head"></span><span class="end-head"></span>' +
      '<span class="end-head">policy</span>' +
      '<span class="end-head">took</span>' +
      '<span class="end-head">commons</span>' +
      '<span class="end-head">score</span>';
    order.forEach(function (i, rank) {
      var leader = !level && i === topIndex;
      var cell = function (value) {
        return '<span class="end-cell' + (leader ? " end-row-winner" : "") +
          '">' + value + "</span>";
      };
      html += '<span class="end-cell rank' +
        (leader ? " end-row-winner" : "") + '">' + (rank + 1) + "</span>" +
        '<span class="end-cell name ' + seatColor(i) +
        (leader ? " end-row-winner" : "") + '">' +
        escapeHtml(aliases[i] || ("Cog " + i)) + "</span>" +
        cell(escapeHtml(clampName(names[i] || ""))) +
        cell(escapeHtml(score((results.total_extracted || [])[i]))) +
        cell(escapeHtml(String((results.public_effort || [])[i] || 0))) +
        cell(escapeHtml(score(scores[i])));
    });
    html += "</div></div>";
    container.innerHTML = html;
    void module;
  }

  function bindFeedToggle(button, startCollapsed) {
    if (!button) return;
    if (startCollapsed) {
      document.body.classList.add("feed-collapsed");
      requestAnimationFrame(function () {
        window.dispatchEvent(new Event("resize"));
      });
    }
    function refresh() {
      button.textContent =
        document.body.classList.contains("feed-collapsed") ? "« LOG" : "LOG »";
    }
    button.onclick = function () {
      document.body.classList.toggle("feed-collapsed");
      refresh();
      window.dispatchEvent(new Event("resize"));
    };
    refresh();
  }

  // ---- Drivers -------------------------------------------------------------

  function stateToView(state, nameMap, effects, extras) {
    var view = effects.view();
    view.seats = applyNames(state.seats, nameMap);
    view.module = state.module || "cleanup";
    view.resource = state.resource || {};
    view.series = state.series || {};
    view.flow = state.flow || [];
    view.r = state.r || 0;
    view.rounds = state.rounds || 0;
    view.phase = state.phase || "";
    view.now = Date.now();
    Object.assign(view, extras || {});
    return view;
  }

  function attachLive(options) {
    // options: {canvas, feed, status, clock, scorebug, endscreen, assetBase,
    //           wsPath, onFrame}
    makeRenderer(options.canvas, options.assetBase, function (renderer) {
      var latest = null;
      var nameMap = makeNameMap([], null);
      var effects = makeEffects();
      var scheme = location.protocol === "https:" ? "wss://" : "ws://";
      var url = scheme + location.host + options.wsPath;

      function setStatus(text, live) {
        if (!options.status) return;
        options.status.textContent = text;
        options.status.classList.toggle("live", !!live);
      }

      function connect() {
        var socket = new WebSocket(url);
        socket.onmessage = function (frame) {
          var data = JSON.parse(frame.data);
          if (data.type === "state" || data.type === "final") {
            latest = data;
            nameMap = makeNameMap(data.aliases, data.player_names);
            if (options.clock) {
              options.clock.textContent = matchHeader(data);
            }
            updateScorebug(options.scorebug, data, nameMap);
          }
          if (options.onFrame) options.onFrame(data);
        };
        socket.onclose = function () {
          setStatus("disconnected", false);
          setTimeout(connect, 2000);
        };
        socket.onopen = function () { setStatus("live", true); };
      }
      connect();

      (function frame() {
        if (latest) {
          renderer.draw(stateToView(latest, nameMap, effects,
            { done: !!latest.done }));
        }
        requestAnimationFrame(frame);
      })();
    });
  }

  // ---- Scrubber ------------------------------------------------------------

  var BEAT_KIND = {
    round_open: "round",
    chat: "chat",
    sanction: "sanction",
    collapse: "collapse",
    patch_dead: "patchdead",
    fallback: "fallback",
    episode_end: "end"
  };

  function beatLabel(event, nameMap) {
    var who = typeof event.slot === "number" && nameMap ?
      nameMap.alias(event.slot) : "";
    switch (event.kind) {
      case "round_open": return "Round " + ((event.r || 0) + 1);
      case "chat": return who + " speaks";
      case "sanction": return who + " punishes " +
        (event.target_alias || "another cog");
      case "collapse": return "The orchard dies";
      case "patch_dead": return "Patch " + event.patch + " stripped bare";
      case "fallback": return who + " falls back to a scripted plan";
      case "episode_end": return "Final";
      default: return event.kind;
    }
  }

  // A click/drag-to-seek track with one span per round. Every beat is a real
  // labelled BUTTON that seeks to its own event, so the transport is usable
  // with a keyboard and readable by a screen reader.
  function buildScrub(container, events, onSeek, nameMap) {
    if (!container) return { update: function () {} };
    container.innerHTML = "";
    var track = document.createElement("div");
    track.className = "scrub-track";
    container.appendChild(track);
    var fill = document.createElement("div");
    fill.className = "scrub-fill";
    container.appendChild(fill);
    var blockStarts = [];
    var lastBlock = null;
    events.forEach(function (event, i) {
      var block = event.kind === "episode_start" ? -1 : (event.r || 0);
      if (block !== lastBlock) {
        blockStarts.push(i);
        lastBlock = block;
      }
    });
    blockStarts.forEach(function (startIdx, r) {
      var endIdx = r + 1 < blockStarts.length ? blockStarts[r + 1] : events.length;
      var span = document.createElement("div");
      span.className = "round-span" + (r % 2 ? " alt" : "");
      span.style.left = (startIdx / events.length * 100) + "%";
      span.style.width = ((endIdx - startIdx) / events.length * 100) + "%";
      container.appendChild(span);
      if (r > 0 && r % 4 === 0) {
        var sep = document.createElement("div");
        sep.className = "round-sep";
        sep.style.left = (startIdx / events.length * 100) + "%";
        container.appendChild(sep);
      }
    });
    events.forEach(function (event, i) {
      var kind = BEAT_KIND[event.kind];
      if (!kind) return;
      var label = beatLabel(event, nameMap);
      var marker = document.createElement("button");
      marker.type = "button";
      marker.className = "beat-marker " + kind +
        (typeof event.slot === "number" ?
          " seat" + (event.slot % COLORS.length) : "");
      marker.setAttribute("aria-label", label);
      marker.title = label;
      marker.style.left = ((i + 1) / events.length * 100) + "%";
      marker.onclick = function (evt) {
        evt.stopPropagation();
        onSeek(i + 1);
      };
      container.appendChild(marker);
    });
    var head = document.createElement("div");
    head.className = "scrub-head";
    container.appendChild(head);

    function seekFromEvent(evt) {
      var rect = container.getBoundingClientRect();
      if (!rect.width) return;   // hidden/unlaid-out page: nothing to seek
      var x = (evt.touches ? evt.touches[0].clientX : evt.clientX) - rect.left;
      var fraction = Math.max(0, Math.min(x / rect.width, 1));
      onSeek(Math.round(fraction * events.length));
    }
    var dragging = false;
    container.addEventListener("pointerdown", function (evt) {
      if (evt.target && evt.target.classList.contains("beat-marker")) return;
      dragging = true;
      try { container.setPointerCapture(evt.pointerId); } catch (ignore) {}
      seekFromEvent(evt);
    });
    container.addEventListener("pointermove", function (evt) {
      if (dragging) seekFromEvent(evt);
    });
    container.addEventListener("pointerup", function () { dragging = false; });

    return {
      update: function (index) {
        var pct = events.length ? (index / events.length * 100) : 0;
        fill.style.width = pct + "%";
        head.style.left = pct + "%";
      }
    };
  }

  function attachReplay(options) {
    // options: {canvas, feed, scrub, playButton, label, clock, scorebug,
    //           endscreen, assetBase, payload}
    var payload = options.payload;
    var events = payload.events || [];
    var states = payload.states || [];
    var nameMap = makeNameMap(payload.names, payload.policyNames);
    var index = 0;
    var playing = true;
    var lastStep = 0;

    makeRenderer(options.canvas, options.assetBase, function (renderer) {
      var effects = makeEffects();
      var scrub = buildScrub(options.scrub, events, function (next) {
        playing = false;
        // Every seek dismisses the endcard, on top of updateEndscreen's own
        // "only at the last event" rule.
        if (options.endscreen) options.endscreen.classList.remove("show");
        setIndex(next, true);
      }, nameMap);
      if (options.playButton) {
        options.playButton.onclick = function () {
          playing = !playing;
          if (playing && index >= events.length) setIndex(0, true);
        };
      }

      function currentState() {
        return states[Math.min(index, states.length - 1)] ||
          { seats: [], phase: "", r: 0, module: payload.module };
      }

      function setIndex(next, jumped) {
        index = Math.max(0, Math.min(next, events.length));
        scrub.update(index);
        if (jumped) effects.reset();
        effects.absorb(events.slice(0, index), jumped);
        var state = currentState();
        if (options.feed) renderFeed(options.feed, events, nameMap, index);
        if (options.label) {
          options.label.textContent = index + " / " + events.length;
        }
        if (options.clock) options.clock.textContent = matchHeader(state);
        updateScorebug(options.scorebug, state, nameMap);
        if (window.cfModuleBar) window.cfModuleBar(state);
        if (window.cfPatchGrid) window.cfPatchGrid(state);
        updateEndscreen(options.endscreen, payload.results,
          index >= events.length && events.length > 0, nameMap, state.module);
      }
      setIndex(0, true);

      (function frame(timestamp) {
        // Dwell on what the viewer is currently looking at: a settled round
        // gets read, a chat line a little longer, a decision less.
        var shown = index > 0 ? events[index - 1] : null;
        var stepMs = shown && shown.kind === "round_end" ? 1500 :
          shown && shown.kind === "chat" ? 1100 :
          shown && shown.kind === "resolve" ? 900 :
          shown && shown.kind === "episode_end" ? 1500 :
          450;
        if (playing && index < events.length && timestamp - lastStep > stepMs) {
          lastStep = timestamp;
          setIndex(index + 1, false);
        }
        if (options.playButton) {
          var running = playing && index < events.length;
          options.playButton.textContent = running ? "❚❚" : "▶";
          options.playButton.classList.toggle("on", running);
        }
        renderer.draw(stateToView(currentState(), nameMap, effects, {
          done: index >= events.length && events.length > 0
        }));
        requestAnimationFrame(frame);
      })(0);

      document.documentElement.setAttribute("data-replay-loaded", "true");
    });
  }

  window.CommonsRenderer = {
    attachLive: attachLive,
    attachReplay: attachReplay,
    renderFeed: renderFeed,
    bindFeedToggle: bindFeedToggle,
    makeNameMap: makeNameMap
  };
})();
