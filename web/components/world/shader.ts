// The world: one full-screen fragment shader, no library (option A, chosen 2026-09-30).
// Weights for each world ease between 0 and 1, so every change is a smooth transformation.
import type { World } from "./worlds";

const FRAG = `
precision highp float;
uniform vec2 R; uniform float T, M;
uniform float wPre, wLive, wWait, wCash, wWon, wLost, wCalm, wStatic;
uniform float S; uniform vec2 SC;
float h(vec2 p){ return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
float n(vec2 p){ vec2 i=floor(p), f=fract(p); f=f*f*(3.-2.*f);
  return mix(mix(h(i), h(i+vec2(1,0)), f.x), mix(h(i+vec2(0,1)), h(i+vec2(1,1)), f.x), f.y); }
float fbm(vec2 p){ float a=.5, s=0.; for(int i=0;i<4;i++){ s+=a*n(p); p*=2.03; a*=.5; } return s; }
float beam(vec2 uv, vec2 o, float ang, float width){
  vec2 d = uv - o; float a = atan(d.x, d.y);
  return exp(-pow((a - ang) / width, 2.)) * smoothstep(1.3, 0., length(d)) * smoothstep(0., .06, d.y);
}
float dots(vec2 uv, float scale, float dir, float t, float thr, float size){
  vec2 g = uv * scale; g.y += dir * t; vec2 id = floor(g), f = fract(g) - .5; float r = h(id);
  vec2 o = vec2(r - .5, fract(r * 13.) - .5) * .6;
  return smoothstep(size, 0., length(f - o)) * step(thr, r);
}
vec3 confetti(vec2 uv, float t){
  vec3 acc = vec3(0.);
  for (int i = 0; i < 2; i++) {
    float fi = float(i); vec2 g = uv * vec2(9. + fi * 4., 7. + fi * 3.);
    g.y += t * (1.2 + fi * .5); g.x += sin(t + g.y * .7) * .25;
    vec2 id = floor(g), f = fract(g) - .5; float r = h(id + fi * 9.);
    if (r < .55) continue;
    float a = t * (2. + r * 4.) + r * 6.28; mat2 m = mat2(cos(a), -sin(a), sin(a), cos(a));
    vec2 q = m * (f - vec2(r - .5, fract(r * 7.) - .5) * .5);
    float piece = step(abs(q.x), .12) * step(abs(q.y), .05 + .04 * abs(sin(a)));
    float c = fract(r * 31.);
    vec3 col = c < .3 ? vec3(1., .77, .24) : c < .5 ? vec3(1.) : c < .75 ? vec3(.44, .89, .71) : vec3(.72, .61, 1.);
    acc += col * piece;
  }
  return acc;
}
void main(){
  vec2 uv = gl_FragCoord.xy / R; vec2 p = (gl_FragCoord.xy - .5 * R) / R.y;
  float t = T * M; float ar = R.x / R.y;
  float lit = wLive + wWait * .4 + wCash * .8 + wWon * .3;
  vec3 pre  = mix(vec3(.02,.03,.08), vec3(.05,.08,.21), uv.y);
  vec3 live = mix(vec3(.02,.06,.06), vec3(.015,.03,.09), uv.y);
  vec3 cash = mix(vec3(.45,.24,.04), vec3(.12,.05,.04), uv.y);
  vec3 won  = mix(vec3(.72,.42,.18), vec3(.16,.13,.28), uv.y);
  vec3 lost = mix(vec3(.11,.11,.12), vec3(.035,.035,.04), uv.y);
  vec3 calm = mix(vec3(.07,.08,.11), vec3(.04,.045,.07), uv.y) + fbm(p * 2. + t * .03) * .04;
  vec3 col = pre * wPre + live * (wLive + wWait) + cash * wCash + won * wWon + lost * wLost
           + calm * wCalm + vec3(.13,.14,.16) * wStatic;
  float st = step(.9965, h(floor(gl_FragCoord.xy / 2.5)));
  col += st * (.55 + .45 * sin(t * 2. + h(floor(gl_FragCoord.xy)) * 40.)) * smoothstep(.5, 1., uv.y) * (wPre + .2 * wLive + .3 * wWon);
  vec2 mc = vec2(.76 * ar, .86); vec2 mp = vec2(uv.x * ar, uv.y);
  float md = length(mp - mc);
  float moon = smoothstep(.055, .05, md) * (1. - smoothstep(.055, .03, length(mp - mc - vec2(.02, .012))) * .85);
  col += (vec3(1., .96, .85) * moon + vec3(.5, .6, 1.) * exp(-md * 14.) * .25) * wPre;
  float cl = fbm(vec2(uv.x * 3. + t * .03, uv.y * 6.)) * smoothstep(.6, .8, uv.y) * smoothstep(1., .82, uv.y);
  col += vec3(.25, .3, .5) * cl * .35 * (wPre + wLost * .6 + wCalm * .5);
  vec2 sp = vec2((uv.x - .5) * ar, uv.y - .5);
  float sun = exp(-length(sp) * 5.);
  float rays = pow(max(0., sin(atan(sp.x, sp.y) * 14. + t * .2)), 8.) * exp(-length(sp) * 2.5);
  col += (vec3(1., .75, .35) * sun * .9 + vec3(1., .8, .5) * rays * .25) * wWon;
  float rim = .5 + .07 * pow(abs(uv.x - .5) * 2., 1.6);
  float stands = step(uv.y, rim);
  vec3 standCol = mix(vec3(.02,.025,.04), vec3(.05,.06,.09), smoothstep(rim - .25, rim, uv.y));
  standCol = mix(standCol, standCol * .6 + vec3(.25,.16,.04) * .3, wCash + wWon * .5);
  col = mix(col, standCol, stands * .92);
  float band = smoothstep(rim - .16, rim - .12, uv.y) * step(uv.y, rim - .01);
  float fl = h(floor(gl_FragCoord.xy / 4.) + floor(t * 7.));
  col += vec3(1.) * step(.992, fl) * band * (wLive * .9 + wCash * 1.6 + wWait * .3);
  col += vec3(.9, .8, .6) * dots(uv, 90., 0., 0., .82, .18) * band * .12 * lit;
  for (int i = 0; i < 2; i++) {
    float x = i == 0 ? .1 : .9;
    float pole = step(abs(uv.x - x), .004) * step(uv.y, .76) * step(rim - .02, uv.y);
    col = mix(col, vec3(.015,.02,.03), pole);
    vec2 lp = vec2((uv.x - x) * ar, uv.y - .775);
    float panel = step(abs(lp.x), .045) * step(abs(lp.y), .02);
    col = mix(col, vec3(.03), panel);
    float bulbs = dots(vec2(lp.x + .045, lp.y + .02), 44., 0., 0., 0., .3) * panel;
    col += vec3(1., .97, .85) * bulbs * (.15 + lit * 1.4);
    col += vec3(.8, .9, 1.) * exp(-length(lp) * 18.) * lit * .5;
  }
  vec2 bu = vec2(uv.x, 1. - uv.y);
  float beams = beam(bu, vec2(.1, .225), .55 + sin(t * .45) * .2, .1) + beam(bu, vec2(.9, .225), -.55 + sin(t * .4 + 2.) * .2, .1);
  col += vec3(.75, .88, 1.) * beams * .2 * (wLive + wCash * .4);
  float scan = beam(uv, vec2(.5, -.1), sin(t * 1.3) * .7, .06);
  col += vec3(.55, .75, 1.) * scan * .5 * wWait;
  float d = length(p - SC);
  float on = step(.001, S);
  float ring = exp(-pow((d - S * 1.5) * 16., 2.)) * (1. - S) * on;
  col += vec3(1., .78, .3) * ring * 1.6 * M;
  float burst = pow(max(0., sin(atan(p.x - SC.x, p.y - SC.y) * 10. - t * .8)), 12.) * exp(-d * 1.6);
  col += vec3(1., .8, .4) * burst * .35 * wCash * M;
  col += confetti(uv, t) * (wCash * .9 + wWon * .2) * M;
  col += vec3(1., .82, .4) * dots(uv, 11., -1., t * .8, .6, .06) * (wCash * .6 + wWon * .3) * M;
  float g = dot(col, vec3(.3, .59, .11));
  col = mix(col, vec3(g), wLost * .85);
  vec2 rg = uv * vec2(70., 5.); rg.y += t * 3.; rg.x += rg.y * .08;
  float rain = step(.93, h(floor(rg))) * smoothstep(.0, .4, fract(rg.y)) * smoothstep(1., .6, fract(rg.y));
  col += vec3(.55, .6, .7) * rain * .12 * wLost * M;
  col += (h(uv * R + fract(t * 7.)) - .5) * .2 * wStatic;
  col *= 1. - .35 * length(uv - .5);
  col += (h(uv * R + t) - .5) * .012;
  gl_FragColor = vec4(col, 1.);
}`;

const UNIFORM_OF: Record<World, string> = {
  pre: "wPre", live: "wLive", wait: "wWait", cash: "wCash", won: "wWon", lost: "wLost",
  calm: "wCalm", static: "wStatic",
};

export interface Shock { p: number; x: number; y: number }

export interface WorldRenderer {
  resize(): void;
  lowerQuality(): boolean;  // false once it can't go lower
  draw(t: number, weights: Record<World, number>, shock: Shock, motion: number): void;
  destroy(): void;
}

export function makeWorld(canvas: HTMLCanvasElement): WorldRenderer | null {
  const gl = canvas.getContext("webgl", { antialias: false, alpha: false, powerPreference: "low-power" });
  if (!gl) return null;
  const compile = (type: number, src: string) => {
    const s = gl.createShader(type)!; gl.shaderSource(s, src); gl.compileShader(s); return s;
  };
  const prog = gl.createProgram()!;
  gl.attachShader(prog, compile(gl.VERTEX_SHADER, "attribute vec2 a;void main(){gl_Position=vec4(a,0,1);}"));
  gl.attachShader(prog, compile(gl.FRAGMENT_SHADER, FRAG));
  gl.linkProgram(prog);
  if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) return null;
  gl.useProgram(prog);
  gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
  gl.enableVertexAttribArray(0);
  gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
  const loc = (k: string) => gl.getUniformLocation(prog, k);
  const u = { R: loc("R"), T: loc("T"), M: loc("M"), S: loc("S"), SC: loc("SC") };
  const w = Object.fromEntries(Object.entries(UNIFORM_OF).map(([k, name]) => [k, loc(name)]));
  let scale = 1;
  const resize = () => {
    const r = canvas.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 2) * scale;
    canvas.width = Math.max(1, Math.round(r.width * dpr));
    canvas.height = Math.max(1, Math.round(r.height * dpr));
    gl.viewport(0, 0, canvas.width, canvas.height);
  };
  resize();
  return {
    resize,
    lowerQuality() {
      if (scale <= 0.5) return false;
      scale = Math.max(0.5, scale * 0.75);
      resize();
      return true;
    },
    draw(t, weights, shock, motion) {
      gl.uniform2f(u.R, canvas.width, canvas.height);
      gl.uniform1f(u.T, t);
      gl.uniform1f(u.M, motion);
      for (const k of Object.keys(UNIFORM_OF) as World[]) gl.uniform1f(w[k], weights[k]);
      gl.uniform1f(u.S, shock.p);
      gl.uniform2f(u.SC, shock.x, shock.y);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
    },
    destroy() {
      gl.getExtension("WEBGL_lose_context")?.loseContext();
    },
  };
}
